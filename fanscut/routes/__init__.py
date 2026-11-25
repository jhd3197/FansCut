from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request, Response

from ..core import Core


def get_core(request: Request) -> Core:
    core: Core = request.app.state.core
    return core


CoreDep = Annotated[Core, Depends(get_core)]


def deprecate(sunset: str, link: str = "") -> Any:
    """A route dependency marking it deprecated (RFC 8594): Deprecation: true,
    Sunset: <HTTP date>, and a Link to its replacement."""
    def dep(response: Response) -> None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = sunset
        if link:
            response.headers["Link"] = f'<{link}>; rel="successor-version"'
    return Depends(dep)


def paginate(items: list[Any], skip: int | None, take: int | None) -> tuple[list[Any],
                                                                         dict[str, int] | None]:
    """(the page, page_info), or (all items, None) when no paging was asked for.

    Lists stay whole by default, so existing callers are unaffected; ?take= turns
    paging on and the response gains a page_info {skip, take, results, pages}."""
    if take is None:
        return items, None
    skip = max(0, skip or 0)
    take = max(1, min(take, 1000))
    return items[skip:skip + take], {"skip": skip, "take": take, "results": len(items),
                                     "pages": -(-len(items) // take)}
