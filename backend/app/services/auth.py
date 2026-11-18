"""凭证管理业务规则：状态流转、字段校验与筛选口径都收在这里。

认证服务与责任覆盖数据快照的联动：
- 续期凭证前先读同一份责任覆盖快照，仅当数据源在当前团队可续期集合内才放行；
- 越出归属的请求不改动任何凭证，只写入拒绝记录；
- 登记认证凭证同样以快照为准，没有当前版本快照时不生成数据源配置记录；
- 另一个入口提供快照核对状态查询，供前端直接展示核对结果。
"""
from __future__ import annotations

from typing import Any

from app.services.coverage import CoverageError, coverage_service
from app.store import store

MODULE = "auth"
LIST_FIELDS = ["凭证编号", "凭证类型", "关联数据源", "凭证内容", "有效期至", "创建时间", "最后使用", "凭证状态"]
REQUIRED_FIELDS = ["凭证编号", "凭证类型", "关联数据源"]
STATUS_ORDER = ["有效", "即将过期", "已过期", "已吊销"]
ACTION_RULES = {"续期凭证": "有效", "吊销凭证": "已吊销"}
NEGATIVE_ACTIONS = []


class AuthService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("凭证编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def register_with_coverage(
        self, values: dict[str, Any], team: str, role: str
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """登记认证凭证：认证服务读取同一份责任覆盖快照后才生成数据源配置记录。

        返回 (entry, message, denied)；denied=True 表示已写拒绝记录且未生成任何记录。
        """
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", False
        datasource_id = str(values.get("关联数据源") or "").strip()
        try:
            check = coverage_service.check_renewal_access(team, role, datasource_id)
        except CoverageError as exc:
            return None, str(exc), exc.denied
        entry, _ = self.create_entry(values)
        assert entry is not None
        entry["归属团队"] = team
        entry["快照编号"] = check["快照编号"]
        entry["最后使用"] = entry.get("最后使用") or "—"
        return entry, f"认证凭证已登记，依据责任覆盖快照 {check['快照编号']} 生成配置记录", False

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"认证凭证 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于凭证管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"认证凭证已{action}"

    def renew_with_coverage(
        self, entry_id: int, team: str, role: str
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """续期凭证：先按责任覆盖快照核对可续期集合，越出归属只写拒绝记录。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"认证凭证 {entry_id} 不存在或已归档", False
        datasource_id = str(entry.get("关联数据源") or "").strip()
        try:
            check = coverage_service.check_renewal_access(team, role, datasource_id)
        except CoverageError as exc:
            return None, str(exc), exc.denied
        renewed, message = self.run_action(entry_id, "续期凭证")
        if renewed is not None:
            renewed["快照编号"] = check["快照编号"]
            renewed["归属团队"] = team
        return renewed, message, True

    def coverage_check_status(self, entry_id: int, team: str, role: str) -> dict[str, Any]:
        """另一个入口：读取责任覆盖数据快照并返回凭证的核对状态。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"认证凭证 {entry_id} 不存在或已归档", "核对状态": "无凭证"}
        datasource_id = str(entry.get("关联数据源") or "").strip()
        scope = coverage_service.renewal_scope(team)
        snapshot = coverage_service.current_snapshot(team)
        if not coverage_service._can(role, "view"):
            coverage_service._deny(team, role, datasource_id, "责任覆盖核对",
                                   f"角色「{role}」无查看权限，直接拒绝")
            return {"ok": False, "message": "无责任覆盖查看权限", "核对状态": "越权拒绝",
                    "凭证编号": entry.get("凭证编号"), "数据源编号": datasource_id}
        if snapshot is None:
            return {"ok": False, "message": "团队缺少当前版本责任覆盖快照", "核对状态": "快照缺失",
                    "凭证编号": entry.get("凭证编号"), "数据源编号": datasource_id}
        in_scope = datasource_id in scope
        return {
            "ok": True,
            "message": "核对通过：数据源在本团队可续期集合内" if in_scope else "核对不通过：数据源越出本团队归属",
            "核对状态": "可续期" if in_scope else "越界拒绝",
            "凭证编号": entry.get("凭证编号"),
            "数据源编号": datasource_id,
            "快照编号": snapshot["快照编号"],
            "快照版本": snapshot["快照版本"],
            "快照更新时间": snapshot.get("更新时间"),
            "可续期集合": sorted(scope),
        }


auth_service = AuthService()
