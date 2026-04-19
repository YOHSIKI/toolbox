"""HTMX 対応のテンプレートレンダリングヘルパ。

HX-Request ヘッダが付いていたら、base.html を継承せず content ブロックだけを返す。
各テンプレートは先頭で以下のようにダイナミック継承する:

    {% extends hx_request|default(false) and "base_empty.html" or "base.html" %}

`hx_request` は本ヘルパが context に自動注入する。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def render_page(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    context: dict[str, Any],
) -> HTMLResponse:
    """page テンプレートを描画する。

    `HX-Request: true` が付いていれば、content ブロックのみを返す
    （ダイナミック継承で base_empty.html を使う）。それ以外は通常の base.html 継承。

    HTMX の swap target (`#hx-main`) に対応するため `hx-push-url` の URL は
    元のパス/クエリを維持する必要があり、content ブロックだけを返せば十分。
    """
    hx_request = request.headers.get("hx-request") == "true"
    merged = {**context, "hx_request": hx_request}
    return templates.TemplateResponse(request, name, merged)
