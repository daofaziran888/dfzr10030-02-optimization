"""责任覆盖快照业务规则。

背景：换班后数据源责任配置曾复用上一班的可续期范围，导致新团队继承已移交的数据源。
本服务按责任人与团队归属，预计算每个团队可继承的数据源配置，生成「责任覆盖数据快照」。

两个成立的边界策略（负责人交接与团队筛选条件同时变化时的冲突）：
- rebuild（按团队范围整体重建）：本次快照以新团队范围整体替换旧视图，最容易核对范围完整性；
- incremental（按责任人变化增量更新索引）：保留未变动条目，逐个移除已交接条目。
两者都保证：一次查询只能返回一个责任范围下的完整配置集合。

约定：
- 快照只追加新版本，历史快照与原记录保留，判定以历史版本为准；
- 覆盖条目按权限、归属、共享范围过滤，越权查看直接拒绝并只写拒绝记录；
- 同一快照批次的待审清单仅允许一条「确认」，其余必须「拒绝」；
- 只读角色只能查看，任何改动直接拦下。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

OWNERSHIP_TABLE = "ownership_records"
SNAPSHOT_TABLE = "coverage_snapshots"
ENTRY_TABLE = "coverage_entries"
REVIEW_TABLE = "coverage_reviews"
RENEWAL_TABLE = "coverage_renewals"
DENIAL_TABLE = "coverage_denials"
DATASOURCE_TABLE = "datasource"

# 角色 → 权限矩阵：只读角色只能查看；外部访客无任何权限，用于演示越权直接拒绝。
ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "值班管理员": {"view": True, "operate": True},
    "团队负责人": {"view": True, "operate": True},
    "值班成员": {"view": True, "operate": True},
    "只读角色": {"view": True, "operate": False},
    "外部访客": {"view": False, "operate": False},
}

SHARE_TEAM = "团队内"
SHARE_CROSS = "跨团队共享"
SNAPSHOT_STATUSES = ["待审核", "当前版本", "历史版本", "已驳回"]
REVIEW_STATUSES = ["待审核", "已确认", "已拒绝"]


class CoverageError(Exception):
    """业务拒绝：路由层转成 403，并已写入拒绝记录（适用时）。"""

    def __init__(self, message: str, *, denied: bool = False) -> None:
        super().__init__(message)
        self.denied = denied


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


class CoverageService:
    # ------------------------------------------------------------------ 权限
    def _can(self, role: str, permission: str) -> bool:
        return ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["外部访客"]).get(permission, False)

    def _deny(self, team: str, role: str, datasource_id: str, request_type: str, reason: str) -> dict[str, Any]:
        """越权/越界请求只写入拒绝记录，原记录不做任何改动。"""
        rows = store.rows(DENIAL_TABLE)
        record = {
            "id": _next_id(DENIAL_TABLE),
            "拒绝编号": f"DEN-{datetime.now().strftime('%Y%m%d')}-{len(rows) + 1:03d}",
            "请求方团队": team,
            "请求方角色": role,
            "数据源编号": datasource_id or "-",
            "请求类型": request_type,
            "拒绝原因": reason,
            "记录时间": now_text(),
        }
        rows.append(record)
        return record

    def _require_view(self, team: str, role: str) -> None:
        if not self._can(role, "view"):
            self._deny(team, role, "-", "责任覆盖查看", f"角色「{role}」无责任覆盖查看权限，直接拒绝")
            raise CoverageError(f"角色「{role}」无权查看责任覆盖数据快照", denied=True)

    def _require_operate(self, team: str, role: str, datasource_id: str, request_type: str) -> None:
        if not self._can(role, "view"):
            self._deny(team, role, datasource_id, request_type, f"角色「{role}」无查看权限，直接拒绝")
            raise CoverageError(f"角色「{role}」无权访问责任覆盖数据快照", denied=True)
        if not self._can(role, "operate"):
            self._deny(team, role, datasource_id, request_type, f"只读角色「{role}」只能查看，不能改动责任覆盖配置")
            raise CoverageError(f"只读角色只能查看，不能执行「{request_type}」", denied=True)

    # ------------------------------------------------------- 负责人归属记录
    def current_ownership(self, datasource_id: str) -> dict[str, Any] | None:
        """读取数据源当前生效的唯一责任人归属记录。"""
        matches = [
            row for row in store.rows(OWNERSHIP_TABLE)
            if row.get("数据源编号") == datasource_id and row.get("是否当前")
        ]
        return matches[-1] if matches else None

    def ownership_for_team(self, team: str) -> list[dict[str, Any]]:
        """当前归属于指定团队的全部责任人记录（每个数据源仅当前版本一条）。"""
        return [
            row for row in store.rows(OWNERSHIP_TABLE)
            if row.get("归属团队") == team and row.get("是否当前")
        ]

    def list_ownership(self, team: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(OWNERSHIP_TABLE)
        if team:
            rows = [row for row in rows if row.get("归属团队") == team]
        return sorted(rows, key=lambda row: (row["数据源编号"], int(row["版本"])))

    # ------------------------------------------------------------- 快照查询
    def latest_snapshot(self, team: str) -> dict[str, Any] | None:
        snapshots = [row for row in store.rows(SNAPSHOT_TABLE) if row.get("归属团队") == team]
        return max(snapshots, key=lambda row: int(row["快照版本"]), default=None)

    def list_snapshot_versions(self, team: str) -> list[dict[str, Any]]:
        """全部快照版本（含历史版本，原记录保留），按版本倒序返回。"""
        rows = [row for row in store.rows(SNAPSHOT_TABLE) if row.get("归属团队") == team]
        return sorted(rows, key=lambda row: -int(row["快照版本"]))

    def current_snapshot(self, team: str) -> dict[str, Any] | None:
        for row in sorted(store.rows(SNAPSHOT_TABLE), key=lambda r: -int(r["快照版本"])):
            if row.get("归属团队") == team and row.get("状态") == "当前版本":
                return row
        return None

    def _entries_of(self, snapshot_id: str) -> list[dict[str, Any]]:
        return [row for row in store.rows(ENTRY_TABLE) if row.get("快照编号") == snapshot_id]

    def list_coverage(
        self,
        team: str,
        role: str,
        *,
        share_scope: str | None = None,
        include_shared: bool = True,
    ) -> dict[str, Any]:
        """读取团队可见的责任覆盖快照。

        过滤口径：
        - 归属团队 == 当前团队的条目始终可见；
        - 跨团队共享条目可被其他团队查看，但不可续期（续期在认证服务侧再拦）；
        - 团队内条目对其他团队不可见，越权查看直接拒绝并写拒绝记录。
        一次查询只返回同一个快照（一个责任范围）下的完整配置集合。
        """
        self._require_view(team, role)
        latest = self.latest_snapshot(team)
        # 责任范围变化后旧覆盖记录不能继续可见：当前查询只认「当前版本」快照，
        # 历史/待审/驳回快照的元数据仍返回供核对，但其条目不作为活动视图返回。
        snapshot = self.current_snapshot(team)
        if snapshot is None:
            return {"snapshot": None, "latest_snapshot": latest,
                    "items": [], "shared_items": [], "denied": False}

        entries = self._entries_of(snapshot["快照编号"])
        own_items: list[dict[str, Any]] = []
        for entry in entries:
            if entry.get("归属团队") == team:
                own_items.append(entry)
        # 跨团队共享：其他团队「当前版本」快照里标记为跨团队共享的条目，仅作只读可见。
        shared_items: list[dict[str, Any]] = []
        if include_shared:
            for other in store.rows(SNAPSHOT_TABLE):
                if other.get("状态") != "当前版本" or other.get("归属团队") == team:
                    continue
                for entry in self._entries_of(other["快照编号"]):
                    if entry.get("共享范围") == SHARE_CROSS:
                        shared_items.append(entry)
        if share_scope:
            own_items = [row for row in own_items if row.get("共享范围") == share_scope]
            shared_items = [row for row in shared_items if row.get("共享范围") == share_scope]
        return {
            "snapshot": snapshot,
            "items": own_items,
            "shared_items": shared_items,
            "denied": False,
        }

    def get_coverage_entry(self, team: str, role: str, datasource_id: str) -> dict[str, Any]:
        """单条核对：越权查看（含团队内条目跨团队查看）直接拒绝并落拒绝记录。"""
        self._require_view(team, role)
        snapshot = self.current_snapshot(team)
        if snapshot is not None:
            for entry in self._entries_of(snapshot["快照编号"]):
                if entry.get("数据源编号") == datasource_id and entry.get("归属团队") == team:
                    return entry
        # 跨团队共享条目可只读核对；其余情况按越权查看拒绝。
        for other in store.rows(SNAPSHOT_TABLE):
            if other.get("状态") != "当前版本" or other.get("归属团队") == team:
                continue
            for entry in self._entries_of(other["快照编号"]):
                if entry.get("数据源编号") == datasource_id and entry.get("共享范围") == SHARE_CROSS:
                    return entry
        reason = f"数据源 {datasource_id} 不在团队 {team} 的责任覆盖快照内，旧覆盖记录已不可见"
        self._deny(team, role, datasource_id, "责任覆盖查看", reason)
        raise CoverageError(reason, denied=True)

    # ------------------------------------------------------------- 快照生成
    def rebuild_snapshot(self, team: str, role: str, mode: str) -> dict[str, Any]:
        """按责任范围重算责任覆盖数据快照。

        rebuild：以新团队范围整体替换旧视图，重新计算全部覆盖条目；
        incremental：以上一版快照为索引，保留未变动条目、移除已交接条目、追加新条目。
        历史快照与旧覆盖条目原样保留，仅把状态置为历史版本。
        """
        self._require_operate(team, role, "-", "责任覆盖快照重算")
        if mode not in ("rebuild", "incremental"):
            raise CoverageError("边界模式仅支持 rebuild（整体重建）或 incremental（增量更新）")

        ownership_rows = self.ownership_for_team(team)
        if not ownership_rows:
            raise CoverageError(f"团队 {team} 当前没有任何责任人归属记录，无法生成覆盖快照")

        # 每条数据源配置必须有唯一责任人：同一数据源只允许一条当前生效归属记录，
        # 且数据源配置本身必须存在（输入来自数据源配置与负责人归属记录）。
        seen: dict[str, str] = {}
        datasource_ids = {str(row.get("源端编号")) for row in store.rows(DATASOURCE_TABLE)}
        problems: list[str] = []
        for ownership in ownership_rows:
            datasource_id = ownership["数据源编号"]
            if datasource_id in seen:
                problems.append(f"{datasource_id} 存在多个当前责任人（{seen[datasource_id]}、{ownership['责任人']}）")
            else:
                seen[datasource_id] = ownership["责任人"]
            if datasource_id not in datasource_ids:
                problems.append(f"{datasource_id} 在数据源配置中不存在，无法计算可继承配置")
        if problems:
            raise CoverageError("责任人归属不满足唯一责任人口径：" + "；".join(problems))

        previous = self.latest_snapshot(team)
        prev_entries = self._entries_of(previous["快照编号"]) if previous else []
        prev_index = {row["数据源编号"]: row for row in prev_entries}
        current_ids = {row["数据源编号"] for row in ownership_rows}

        stamp = now_text()
        day = datetime.now().strftime("%Y%m%d")
        seq = len(store.rows(SNAPSHOT_TABLE)) + 1
        snapshot_id = f"SNAP-{day}-{seq:03d}"
        version = int(previous["快照版本"]) + 1 if previous else 1

        new_entries: list[dict[str, Any]] = []
        changes: list[dict[str, str]] = []
        entry_rows = store.rows(ENTRY_TABLE)

        if mode == "rebuild":
            # 整体重建：当前责任范围全量重算，旧视图整体替换（旧记录保留在历史快照里）。
            for ownership in sorted(ownership_rows, key=lambda row: row["数据源编号"]):
                record = self._make_entry(snapshot_id, ownership, entry_rows)
                new_entries.append(record)
                old = prev_index.get(ownership["数据源编号"])
                if old is None:
                    changes.append({"数据源编号": ownership["数据源编号"], "变更类型": "新增覆盖",
                                    "责任人": ownership["责任人"], "来源记录版本": ownership["版本"]})
                elif old.get("责任人") != ownership["责任人"] or old.get("归属团队") != ownership["归属团队"]:
                    changes.append({"数据源编号": ownership["数据源编号"], "变更类型": "责任人/团队变更",
                                    "责任人": ownership["责任人"], "来源记录版本": ownership["版本"]})
            for datasource_id in sorted(set(prev_index) - current_ids):
                old = prev_index[datasource_id]
                changes.append({"数据源编号": datasource_id, "变更类型": "移交移除",
                                "责任人": old.get("责任人", ""), "来源记录版本": old.get("来源记录版本", 1)})
        else:
            # 增量更新：未变动条目保留并直接挂到新快照版本，已交接条目逐个移除，新条目追加。
            for ownership in sorted(ownership_rows, key=lambda row: row["数据源编号"]):
                old = prev_index.get(ownership["数据源编号"])
                if old and old.get("责任人") == ownership["责任人"] and old.get("归属团队") == ownership["归属团队"] \
                        and str(old.get("来源记录版本")) == str(ownership["版本"]):
                    kept = {**old, "id": _next_id(ENTRY_TABLE), "快照编号": snapshot_id, "覆盖状态": "已覆盖(沿用)"}
                    entry_rows.append(kept)
                    new_entries.append(kept)
                else:
                    record = self._make_entry(snapshot_id, ownership, entry_rows)
                    new_entries.append(record)
                    change_type = "责任人变更" if old else "新增覆盖"
                    changes.append({"数据源编号": ownership["数据源编号"], "变更类型": change_type,
                                    "责任人": ownership["责任人"], "来源记录版本": ownership["版本"]})
            for datasource_id in sorted(set(prev_index) - current_ids):
                old = prev_index[datasource_id]
                changes.append({"数据源编号": datasource_id, "变更类型": "移交移除",
                                "责任人": old.get("责任人", ""), "来源记录版本": old.get("来源记录版本", 1)})

        snapshot = {
            "id": _next_id(SNAPSHOT_TABLE),
            "快照编号": snapshot_id,
            "归属团队": team,
            "边界模式": "rebuild" if mode == "rebuild" else "incremental",
            "快照版本": version,
            "生成时间": stamp,
            "覆盖数量": len(new_entries),
            "状态": "待审核",
            "更新时间": stamp,
            "备注": "整体重建：新团队范围整体替换旧视图" if mode == "rebuild"
            else "增量更新：保留未变动条目，逐个移除已交接条目",
        }
        store.rows(SNAPSHOT_TABLE).append(snapshot)
        if previous is not None and previous.get("状态") != "历史版本":
            previous["状态"] = "历史版本"

        # 重算同时生成待审清单；同批次仅允许一条确认，其余拒绝。
        review_rows = store.rows(REVIEW_TABLE)
        for change in changes:
            review_rows.append({
                "id": _next_id(REVIEW_TABLE),
                "待审编号": f"REV-{day}-{len(review_rows) + 1:03d}",
                "快照编号": snapshot_id,
                "归属团队": team,
                "数据源编号": change["数据源编号"],
                "责任人": change["责任人"],
                "变更类型": change["变更类型"],
                "来源记录版本": change["来源记录版本"],
                "审核状态": "待审核",
                "审核意见": "",
                "提交时间": stamp,
            })
        return {"snapshot": snapshot, "changes": changes, "entries": new_entries}

    def _make_entry(self, snapshot_id: str, ownership: dict[str, Any], entry_rows: list[dict[str, Any]]) -> dict[str, Any]:
        record = {
            "id": _next_id(ENTRY_TABLE),
            "快照编号": snapshot_id,
            "数据源编号": ownership["数据源编号"],
            "责任人": ownership["责任人"],
            "归属团队": ownership["归属团队"],
            "共享范围": ownership.get("共享范围", SHARE_TEAM),
            "来源记录版本": ownership["版本"],
            "覆盖状态": "已覆盖",
        }
        entry_rows.append(record)
        return record

    # ------------------------------------------------------------- 待审清单
    def list_reviews(self, team: str, role: str, status: str | None = None) -> list[dict[str, Any]]:
        self._require_view(team, role)
        rows = [row for row in store.rows(REVIEW_TABLE) if row.get("归属团队") == team]
        if status:
            rows = [row for row in rows if row.get("审核状态") == status]
        return rows

    def decide_review(self, team: str, role: str, review_id: int, decision: str, remark: str | None) -> dict[str, Any]:
        """审核待审条目。

        规则：同一快照批次仅一条「确认」；已有确认后再提交确认直接拦下，要求改为拒绝。
        只读角色不能改动；全部拒绝时快照驳回，沿用上一版历史快照。
        """
        self._require_operate(team, role, "-", "责任覆盖审核")
        if decision not in ("确认", "拒绝"):
            raise CoverageError("审核结论只支持「确认」或「拒绝」")
        review = store.find(REVIEW_TABLE, review_id)
        if review is None or review.get("归属团队") != team:
            raise CoverageError(f"待审记录 {review_id} 不存在或不属于团队 {team}")
        if review.get("审核状态") != "待审核":
            raise CoverageError(f"待审记录已处理为「{review.get('审核状态')}」，不能重复改动，原记录保留")

        snapshot_id = review.get("快照编号")
        siblings = [row for row in store.rows(REVIEW_TABLE) if row.get("快照编号") == snapshot_id]
        if decision == "确认" and any(row.get("审核状态") == "已确认" for row in siblings):
            self._deny(team, role, review.get("数据源编号", "-"), "责任覆盖审核",
                       "同一快照批次仅允许一条确认，其余必须拒绝")
            raise CoverageError("同一快照批次已有一条确认，本条只能拒绝", denied=True)

        review["审核状态"] = "已确认" if decision == "确认" else "已拒绝"
        review["审核意见"] = (remark or "").strip() or ("责任覆盖核对通过" if decision == "确认" else "核对不通过，拒绝覆盖")
        review["审核时间"] = now_text()

        snapshot = next((row for row in store.rows(SNAPSHOT_TABLE) if row.get("快照编号") == snapshot_id), None)
        pending = [row for row in siblings if row.get("审核状态") == "待审核"]
        if snapshot is not None and not pending:
            confirmed = any(row.get("审核状态") == "已确认" for row in siblings)
            snapshot["状态"] = "当前版本" if confirmed else "已驳回"
            snapshot["更新时间"] = now_text()
            snapshot["审核备注"] = "批次审核完成：一条确认、其余拒绝" if confirmed else "批次全部拒绝，快照驳回"
        return review

    # ------------------------------------------------------------- 拒绝记录
    def list_denials(self, team: str, role: str) -> list[dict[str, Any]]:
        self._require_view(team, role)
        return [row for row in store.rows(DENIAL_TABLE) if row.get("请求方团队") == team]

    # ------------------------------------------------------------- 团队核对
    def team_summary(self, team: str, role: str) -> dict[str, Any]:
        """团队页核对：覆盖数量、越权拒绝记录数、覆盖更新时间。"""
        self._require_view(team, role)
        snapshot = self.latest_snapshot(team)
        current = self.current_snapshot(team)
        coverage_count = len(self._entries_of(current["快照编号"])) if current else 0
        denials = [row for row in store.rows(DENIAL_TABLE) if row.get("请求方团队") == team]
        pending_reviews = [
            row for row in store.rows(REVIEW_TABLE)
            if row.get("归属团队") == team and row.get("审核状态") == "待审核"
        ]
        return {
            "team": team,
            "role": role,
            "只读": not self._can(role, "operate"),
            "current_snapshot": current,
            "latest_snapshot": snapshot,
            "coverage_count": coverage_count,
            "denial_count": len(denials),
            "pending_review_count": len(pending_reviews),
            "coverage_updated_at": current.get("更新时间") if current else None,
        }

    # ----------------------------------------------------- 认证服务复用入口
    def renewal_scope(self, team: str) -> set[str]:
        """认证服务口径：可续期数据源集合 = 当前版本快照内本团队归属的条目。

        跨团队共享条目仅可查看，不在可续期集合内；没有当前版本快照时返回空集合，
        认证服务此时不得生成数据源配置记录。
        """
        snapshot = self.current_snapshot(team)
        if snapshot is None:
            return set()
        return {
            row["数据源编号"]
            for row in self._entries_of(snapshot["快照编号"])
            if row.get("归属团队") == team
        }

    def check_renewal_access(self, team: str, role: str, datasource_id: str) -> dict[str, Any]:
        """认证服务续期前核对：只读角色拒绝、越出归属只写拒绝记录。"""
        if not self._can(role, "view") or not self._can(role, "operate"):
            reason = f"角色「{role}」无权续期凭证（只读角色只能查看）"
            self._deny(team, role, datasource_id, "凭证续期", reason)
            raise CoverageError(reason, denied=True)
        snapshot = self.current_snapshot(team)
        if snapshot is None:
            reason = f"团队 {team} 缺少已审核通过的当前版本责任覆盖快照，禁止续期"
            self._deny(team, role, datasource_id, "凭证续期", reason)
            raise CoverageError(reason, denied=True)
        if datasource_id not in self.renewal_scope(team):
            reason = f"数据源 {datasource_id} 越出团队 {team} 的责任归属，续期请求只记录拒绝"
            self._deny(team, role, datasource_id, "凭证续期", reason)
            raise CoverageError(reason, denied=True)
        return {"allowed": True, "快照编号": snapshot["快照编号"], "快照版本": snapshot["快照版本"]}


coverage_service = CoverageService()
