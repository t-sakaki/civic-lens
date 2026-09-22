"""Civic Lens — プロジェクト（共同作業スペース）

情報公開請求・不服審査請求を複数の市民で共同して進めるための単位。
ユーザーは登録時に自動的に「個人プロジェクト」を1つ持ち、
そこに他のメンバーを招待することでチームプロジェクトとしても使える。

保存先はFirestore（`projects` / `project_invites` コレクション）。
"""
import os
import secrets
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel

from firebase_client import get_firestore_client

PROJECTS_COLLECTION = "projects"
INVITES_COLLECTION = "project_invites"

INVITE_EXPIRY_SECONDS = 7 * 24 * 3600  # 招待リンクは7日間有効

ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
VALID_ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER)


class Project(BaseModel):
    """共同作業プロジェクト"""
    project_id: str
    name: str
    description: str = ""
    type: str = "team"  # "personal" | "team"
    owner_id: str
    member_ids: List[str] = []
    member_roles: dict = {}  # user_id -> role
    status: str = "active"  # "active" | "archived"
    created_at: str
    updated_at: str


class ProjectInvite(BaseModel):
    """プロジェクト招待"""
    invite_id: str
    project_id: str
    invited_by: str
    role: str
    token: str
    created_at: str
    expires_at: str
    used_by: Optional[str] = None
    used_at: Optional[str] = None


def _projects_ref():
    return get_firestore_client().collection(PROJECTS_COLLECTION)


def _invites_ref():
    return get_firestore_client().collection(INVITES_COLLECTION)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create_project(
    owner_id: str,
    name: str,
    description: str = "",
    project_type: str = "team",
) -> Project:
    """新規プロジェクトを作成（作成者は自動的にownerとして参加）"""
    clean_name = name.strip()
    if len(clean_name) < 1:
        raise ValueError("プロジェクト名を入力してください")

    now = _now_iso()
    project = Project(
        project_id=f"proj-{secrets.token_hex(8)}",
        name=clean_name,
        description=description.strip(),
        type=project_type,
        owner_id=owner_id,
        member_ids=[owner_id],
        member_roles={owner_id: ROLE_OWNER},
        created_at=now,
        updated_at=now,
    )
    _projects_ref().document(project.project_id).set(project.model_dump())
    return project


def create_personal_project(owner_id: str, display_name: str) -> Project:
    """ユーザー登録時に自動生成する個人プロジェクト"""
    return create_project(
        owner_id=owner_id,
        name=f"{display_name}の個人プロジェクト",
        description="自分のための情報公開請求・不服審査請求",
        project_type="personal",
    )


def get_project(project_id: str) -> Optional[Project]:
    doc = _projects_ref().document(project_id).get()
    if not doc.exists:
        return None
    return Project(**doc.to_dict())


def get_projects_for_user(user_id: str) -> List[Project]:
    """ユーザーが所属する（メンバーである）プロジェクト一覧を取得（左メニュー用）"""
    docs = _projects_ref().where("member_ids", "array_contains", user_id).stream()
    projects = [Project(**d.to_dict()) for d in docs]
    # 個人プロジェクトを先頭、それ以外は更新日時の降順
    projects.sort(key=lambda p: p.updated_at, reverse=True)
    projects.sort(key=lambda p: p.type != "personal")
    return projects


def get_user_role(project: Project, user_id: str) -> Optional[str]:
    return project.member_roles.get(user_id)


def update_project(
    project_id: str,
    requester_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
) -> Project:
    """プロジェクト情報を更新（owner/editorのみ許可）"""
    project = get_project(project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")
    role = get_user_role(project, requester_id)
    if role not in (ROLE_OWNER, ROLE_EDITOR):
        raise PermissionError("この操作を行う権限がありません")

    data = project.model_dump()
    if name is not None:
        clean = name.strip()
        if len(clean) < 1:
            raise ValueError("プロジェクト名を入力してください")
        data["name"] = clean
    if description is not None:
        data["description"] = description.strip()
    if status is not None:
        if status not in ("active", "archived"):
            raise ValueError("statusはactiveまたはarchivedである必要があります")
        if role != ROLE_OWNER:
            raise PermissionError("アーカイブ操作はオーナーのみ実行できます")
        data["status"] = status
    data["updated_at"] = _now_iso()

    _projects_ref().document(project_id).set(data)
    return Project(**data)


def delete_project(project_id: str, requester_id: str) -> None:
    """プロジェクトを削除（ownerのみ）。個人プロジェクトは削除不可"""
    project = get_project(project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")
    if project.owner_id != requester_id:
        raise PermissionError("削除はオーナーのみ実行できます")
    if project.type == "personal":
        raise ValueError("個人プロジェクトは削除できません")
    _projects_ref().document(project_id).delete()


def create_invite(project_id: str, invited_by: str, role: str = ROLE_EDITOR) -> ProjectInvite:
    """プロジェクトへの招待リンクを発行（owner/editorのみ）"""
    if role not in (ROLE_EDITOR, ROLE_VIEWER):
        raise ValueError("招待できる役割はeditorまたはviewerです")

    project = get_project(project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")
    requester_role = get_user_role(project, invited_by)
    if requester_role not in (ROLE_OWNER, ROLE_EDITOR):
        raise PermissionError("招待を発行する権限がありません")

    now = datetime.utcnow()
    invite = ProjectInvite(
        invite_id=f"inv-{secrets.token_hex(8)}",
        project_id=project_id,
        invited_by=invited_by,
        role=role,
        token=secrets.token_urlsafe(24),
        created_at=now.isoformat() + "Z",
        expires_at=(now + timedelta(seconds=INVITE_EXPIRY_SECONDS)).isoformat() + "Z",
    )
    _invites_ref().document(invite.invite_id).set(invite.model_dump())
    return invite


def accept_invite(token: str, user_id: str) -> Project:
    """招待トークンを使ってプロジェクトに参加する"""
    matches = list(_invites_ref().where("token", "==", token).limit(1).stream())
    if not matches:
        raise ValueError("招待リンクが無効です")
    doc = matches[0]
    invite = ProjectInvite(**doc.to_dict())

    if invite.used_by:
        raise ValueError("この招待リンクは既に使用されています")
    if invite.expires_at < _now_iso():
        raise ValueError("この招待リンクの有効期限が切れています")

    project = get_project(invite.project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")

    if user_id not in project.member_ids:
        project.member_ids.append(user_id)
    project.member_roles[user_id] = invite.role
    project.updated_at = _now_iso()
    _projects_ref().document(project.project_id).set(project.model_dump())

    doc.reference.update({"used_by": user_id, "used_at": _now_iso()})
    return project


def remove_member(project_id: str, requester_id: str, target_user_id: str) -> Project:
    """メンバーを除名（ownerのみ。ownerは自分自身を除名できない）"""
    project = get_project(project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")
    if project.owner_id != requester_id:
        raise PermissionError("メンバーの除名はオーナーのみ実行できます")
    if target_user_id == project.owner_id:
        raise ValueError("オーナー自身を除名することはできません")

    project.member_ids = [uid for uid in project.member_ids if uid != target_user_id]
    project.member_roles.pop(target_user_id, None)
    project.updated_at = _now_iso()
    _projects_ref().document(project_id).set(project.model_dump())
    return project


def update_member_role(project_id: str, requester_id: str, target_user_id: str, new_role: str) -> Project:
    """メンバーの役割を変更（ownerのみ）"""
    if new_role not in (ROLE_EDITOR, ROLE_VIEWER):
        raise ValueError("役割はeditorまたはviewerである必要があります")
    project = get_project(project_id)
    if not project:
        raise ValueError("プロジェクトが見つかりません")
    if project.owner_id != requester_id:
        raise PermissionError("役割の変更はオーナーのみ実行できます")
    if target_user_id == project.owner_id:
        raise ValueError("オーナー自身の役割は変更できません")
    if target_user_id not in project.member_ids:
        raise ValueError("指定されたユーザーはメンバーではありません")

    project.member_roles[target_user_id] = new_role
    project.updated_at = _now_iso()
    _projects_ref().document(project_id).set(project.model_dump())
    return project
