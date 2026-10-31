from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..core import Core


def get_core(request: Request) -> Core:
    core: Core = request.app.state.core
    return core


CoreDep = Annotated[Core, Depends(get_core)]
