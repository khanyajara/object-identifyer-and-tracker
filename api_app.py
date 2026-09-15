from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm

from services.auth_service import AdminAuthService
from services.video_service import VideoService


app = FastAPI(title="Roadwatch Vision Recorder API")
service = VideoService()
bearer_scheme = HTTPBearer(auto_error=False)


def current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return AdminAuthService().decode_access_token(credentials.credentials)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_roles(*allowed_roles):
    def dependency(principal: Annotated[dict, Depends(current_principal)]):
        if principal["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role is not authorized for this operation.",
            )
        return principal

    return dependency


@app.post("/auth/token")
def issue_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    account = AdminAuthService().authenticate(form_data.username, form_data.password)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": AdminAuthService().issue_access_token(account),
        "token_type": "bearer",
        "role": account["role"],
    }


@app.get("/health")
def health(
    _: Annotated[dict, Depends(require_roles("user", "viewer", "operator", "admin", "super_admin"))],
):
    return {"status": "ok"}


@app.get("/videos")
def videos(
    _: Annotated[dict, Depends(require_roles("user", "viewer", "operator", "admin", "super_admin"))],
):
    return service.list_videos()


@app.get("/videos/{video_id}")
def video(
    video_id: str,
    _: Annotated[dict, Depends(require_roles("user", "viewer", "operator", "admin", "super_admin"))],
):
    try:
        return service.load(video_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/videos/sync")
def receive_sync(
    payload: dict,
    _: Annotated[dict, Depends(require_roles("operator", "admin", "super_admin"))],
):
    return {"status": "received", "video_id": payload.get("video_id")}
