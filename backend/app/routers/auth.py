"""凭证管理接口：维护认证凭证，覆盖续期凭证、吊销凭证等动作。

续期与登记联动责任覆盖数据快照：
- POST /{id}/renew：按快照核对可续期集合，越出归属只写拒绝记录；
- POST /register：读取同一份快照后才生成数据源配置记录；
- GET /{id}/coverage-check：另一个入口，读取快照返回核对状态。
调用方上下文（team/role）通过 query 参数或 JSON 字段传入，兼容 X-Team / X-Role 请求头。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, PageResult
from app.services.auth import auth_service

router = APIRouter(prefix="/api/auth", tags=["凭证管理"])

LIST_FIELDS = ["凭证编号", "凭证类型", "关联数据源", "凭证内容", "有效期至", "创建时间", "最后使用", "凭证状态"]
STATUSES = ["有效", "即将过期", "已过期", "已吊销"]


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


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按凭证编号检索"),
    status: str | None = Query(default=None, description="有效、即将过期、已过期、已吊销"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、凭证编号关键字与状态过滤凭证管理列表。"""
    items, total = auth_service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = auth_service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="auth.csv"'})


@router.post("/register", response_model=ActionResult)
def register_with_coverage(
    payload: dict[str, object],
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> ActionResult:
    """读取责任覆盖快照后才登记认证凭证；越出归属只写拒绝记录、不生成配置。"""
    actor_team = resolve_team(team or str(payload.get("team") or ""), x_team)
    actor_role = resolve_role(role or str(payload.get("role") or ""), x_role)
    values = {key: value for key, value in payload.items() if key not in ("team", "role")}
    entry, message, denied = auth_service.register_with_coverage(values, actor_team, actor_role)
    if entry is None:
        return ActionResult(ok=False, message=message, entry={"denied": denied})
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/coverage-check")
def coverage_check(
    entry_id: int,
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict:
    """另一个入口：认证服务读取责任覆盖快照并返回核对状态。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    return auth_service.coverage_check_status(entry_id, actor_team, actor_role)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条认证凭证明细；不存在时给出可读的错误说明。"""
    entry = auth_service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"认证凭证 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条认证凭证，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = auth_service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="认证凭证已登记", entry=entry)


@router.post("/{entry_id}/renew", response_model=ActionResult)
def renew_with_coverage(
    entry_id: int,
    team: str | None = Query(default=None),
    role: str | None = Query(default=None),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> ActionResult:
    """续期凭证：按责任覆盖快照确定可续期集合，越出归属只写拒绝记录。"""
    actor_team = resolve_team(team, x_team)
    actor_role = resolve_role(role, x_role)
    entry, message, allowed = auth_service.renew_with_coverage(entry_id, actor_team, actor_role)
    if entry is None:
        return ActionResult(ok=False, message=message, entry={"denied": allowed})
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条认证凭证执行续期凭证、吊销凭证；不允许的动作会被拦下并说明原因。"""
    entry, message = auth_service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
