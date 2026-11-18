"""责任覆盖快照接口：按责任人/团队归属预计算可继承数据源配置并生成覆盖快照。

调用方上下文（当前值班团队、角色）统一通过 query 参数或 JSON 字段传入：
- team：当前值班团队（换班后即新团队）
- role：调用角色（值班管理员/团队负责人/值班成员/只读角色/外部访客）
仍兼容 X-Team / X-Role 请求头（HTTP 头按 latin-1 解码，这里做一次还原）。
只读角色只能 GET；越权访问由服务层直接拒绝并落拒绝记录。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from app.schemas import ActionResult, PageResult
from app.services.coverage import CoverageError, coverage_service

router = APIRouter(prefix="/api/coverage", tags=["责任覆盖快照"])


class RebuildPayload(BaseModel):
    mode: str = "rebuild"  # rebuild=按团队整体重建；incremental=按责任人变化增量更新
    team: str | None = None
    role: str | None = None


class ReviewPayload(BaseModel):
    decision: str  # 确认 / 拒绝
    remark: str | None = None
    team: str | None = None
    role: str | None = None


def _decode_header(value: str | None) -> str | None:
    """Starlette 按 latin-1 解码请求头，UTF-8 中文需要按字节还原。"""
    if not value:
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def resolve_team(team: str | None, x_team: str | None) -> str:
    team = (team or "").strip() or (_decode_header(x_team) or "").strip()
    return team or "未指定团队"


def resolve_role(role: str | None, x_role: str | None) -> str:
    role = (role or "").strip() or (_decode_header(x_role) or "").strip()
    return role or "值班管理员"


@router.get("/summary")
def summary(
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """团队页核对：覆盖数量、越权拒绝记录、覆盖更新时间、待审数量。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    try:
        return ActionResult(ok=True, message="责任覆盖核对信息",
                            entry=coverage_service.team_summary(actor_team, actor_role)).model_dump()
    except CoverageError as exc:
        return ActionResult(ok=False, message=str(exc)).model_dump()


@router.get("/ownership", response_model=PageResult[dict])
def list_ownership(
    filter_team: str | None = Query(default=None, description="按归属团队过滤归属记录"),
    team: str | None = Query(default=None, description="调用方团队"),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> PageResult[dict]:
    """读取负责人归属记录（含历史版本，原记录保留）。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    coverage_service._require_view(actor_team, actor_role)
    rows = coverage_service.list_ownership(filter_team)
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)


@router.get("/snapshots/history", response_model=PageResult[dict])
def list_history(
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> PageResult[dict]:
    """历史快照核对：原记录保留，以历史版本为准判定，不参与当前查询视图。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    coverage_service._require_view(actor_team, actor_role)
    rows = [
        row for row in coverage_service.list_snapshot_versions(actor_team)
        if row.get("状态") == "历史版本"
    ]
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)


@router.get("/snapshots")
def list_coverage(
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    share_scope: str | None = Query(default=None, description="团队内 / 跨团队共享"),
    include_shared: bool = Query(default=True),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """读取本团队责任覆盖数据快照；每个团队只得到本归属源端的配置记录。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    try:
        payload = coverage_service.list_coverage(
            actor_team, actor_role, share_scope=share_scope, include_shared=include_shared)
        return ActionResult(ok=True, message="责任覆盖数据快照", entry=payload).model_dump()
    except CoverageError as exc:
        return ActionResult(ok=False, message=str(exc), entry={"denied": exc.denied}).model_dump()


@router.get("/entries/{datasource_id}")
def get_entry(
    datasource_id: str,
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """单条覆盖核对：越权查看直接拒绝并写拒绝记录。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    try:
        entry = coverage_service.get_coverage_entry(actor_team, actor_role, datasource_id)
        return ActionResult(ok=True, message="覆盖条目核对通过", entry=entry).model_dump()
    except CoverageError as exc:
        return ActionResult(ok=False, message=str(exc), entry={"denied": exc.denied}).model_dump()


@router.post("/snapshots/rebuild")
def rebuild(
    payload: RebuildPayload,
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """跨模块触发：认证服务联动重算责任覆盖快照，并生成待审清单。"""
    actor_team = resolve_team(team or payload.team, x_team)
    actor_role = resolve_role(role or payload.role, x_role)
    try:
        result = coverage_service.rebuild_snapshot(actor_team, actor_role, payload.mode.strip())
        return ActionResult(
            ok=True,
            message=f"责任覆盖快照已按「{'整体重建' if payload.mode == 'rebuild' else '增量更新'}」重算并生成待审清单",
            entry=result,
        ).model_dump()
    except CoverageError as exc:
        return ActionResult(ok=False, message=str(exc)).model_dump()


@router.get("/reviews", response_model=PageResult[dict])
def list_reviews(
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> PageResult[dict]:
    """待审清单：同一快照批次仅一条可确认，其余拒绝。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    rows = coverage_service.list_reviews(actor_team, actor_role, status)
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)


@router.post("/reviews/{review_id}/decision")
def decide_review(
    review_id: int,
    payload: ReviewPayload,
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """提交审核结论：每批次仅一条确认，其余拒绝；只读角色被拒绝。"""
    actor_team = resolve_team(team or payload.team, x_team)
    actor_role = resolve_role(role or payload.role, x_role)
    try:
        review = coverage_service.decide_review(
            actor_team, actor_role, review_id, payload.decision.strip(), payload.remark)
        return ActionResult(ok=True, message=f"待审条目已{review['审核状态']}", entry=review).model_dump()
    except CoverageError as exc:
        return ActionResult(ok=False, message=str(exc), entry={"denied": exc.denied}).model_dump()


@router.get("/denials", response_model=PageResult[dict])
def list_denials(
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> PageResult[dict]:
    """越权/越界拒绝记录，只追加不改写。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    rows = coverage_service.list_denials(actor_team, actor_role)
    return PageResult(items=rows, total=len(rows), page=1, size=len(rows) or 1)
