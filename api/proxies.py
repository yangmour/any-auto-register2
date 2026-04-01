from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
from core.db import ProxyModel, get_session
from core.proxy_pool import proxy_pool
from core.http_client import normalize_proxy_url

router = APIRouter(prefix="/proxies", tags=["proxies"])


class ProxyCreate(BaseModel):
    url: str
    region: str = ""


class ProxyBulkCreate(BaseModel):
    proxies: list[str]
    region: str = ""


@router.get("")
def list_proxies(session: Session = Depends(get_session)):
    items = session.exec(select(ProxyModel)).all()
    return items


@router.post("")
def add_proxy(body: ProxyCreate, session: Session = Depends(get_session)):
    try:
        normalized_url = normalize_proxy_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = session.exec(select(ProxyModel).where(ProxyModel.url == normalized_url)).first()
    if existing:
        raise HTTPException(400, "代理已存在")
    p = ProxyModel(url=normalized_url, region=body.region)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.post("/bulk")
def bulk_add_proxies(body: ProxyBulkCreate, session: Session = Depends(get_session)):
    added = 0
    invalid: list[str] = []
    for url in body.proxies:
        url = url.strip()
        if not url:
            continue
        try:
            normalized_url = normalize_proxy_url(url)
        except ValueError:
            invalid.append(url)
            continue
        existing = session.exec(select(ProxyModel).where(ProxyModel.url == normalized_url)).first()
        if not existing:
            session.add(ProxyModel(url=normalized_url, region=body.region))
            added += 1
    session.commit()
    return {"added": added, "invalid": invalid}


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    session.delete(p)
    session.commit()
    return {"ok": True}


@router.patch("/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    p.is_active = not p.is_active
    session.add(p)
    session.commit()
    return {"is_active": p.is_active}


@router.post("/check")
def check_proxies(background_tasks: BackgroundTasks):
    background_tasks.add_task(proxy_pool.check_all)
    return {"message": "检测任务已启动"}
