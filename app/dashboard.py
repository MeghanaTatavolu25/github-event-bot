from fastapi import APIRouter, Depends, Request

from app.web import current_user, render

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, user: dict = Depends(current_user)):
    return render(request, "dashboard.html", user)
