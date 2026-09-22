import uuid
import pytest
from fastapi.testclient import TestClient
from app import app
import agent
from auth import register_user
from project import (
    create_project, get_project, get_projects_for_user,
    update_project, delete_project, create_invite, accept_invite,
    remove_member, update_member_role, ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_genai_disabled(monkeypatch):
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))


def _new_user():
    uname = f"proj_user_{uuid.uuid4().hex[:6]}"
    return register_user(username=uname, password="password123")


def test_registration_creates_personal_project():
    user = _new_user()
    projects = get_projects_for_user(user.user_id)
    assert len(projects) == 1
    assert projects[0].type == "personal"
    assert projects[0].owner_id == user.user_id
    assert projects[0].member_roles[user.user_id] == ROLE_OWNER


def test_create_team_project_and_invite_flow():
    owner = _new_user()
    member = _new_user()

    project = create_project(owner_id=owner.user_id, name="不服審査請求チーム", project_type="team")
    assert project.type == "team"
    assert project.member_roles[owner.user_id] == ROLE_OWNER

    invite = create_invite(project.project_id, invited_by=owner.user_id, role=ROLE_EDITOR)
    joined = accept_invite(invite.token, user_id=member.user_id)
    assert member.user_id in joined.member_ids
    assert joined.member_roles[member.user_id] == ROLE_EDITOR

    # 招待は使い捨て
    with pytest.raises(ValueError):
        accept_invite(invite.token, user_id=_new_user().user_id)

    # editorは名前を更新できる
    updated = update_project(project.project_id, requester_id=member.user_id, name="改称後")
    assert updated.name == "改称後"

    # viewerへ降格
    update_member_role(project.project_id, requester_id=owner.user_id, target_user_id=member.user_id, new_role=ROLE_VIEWER)
    with pytest.raises(PermissionError):
        update_project(project.project_id, requester_id=member.user_id, name="viewerは変更不可")

    # オーナーのみ削除可能
    remove_member(project.project_id, requester_id=owner.user_id, target_user_id=member.user_id)
    refreshed = get_project(project.project_id)
    assert member.user_id not in refreshed.member_ids


def test_personal_project_cannot_be_deleted():
    user = _new_user()
    personal = get_projects_for_user(user.user_id)[0]
    with pytest.raises(ValueError):
        delete_project(personal.project_id, requester_id=user.user_id)


def test_project_api_endpoints():
    reg = client.post("/api/auth/register", data={"username": f"api_proj_{uuid.uuid4().hex[:6]}", "password": "password123"})
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    list_res = client.get("/api/projects", headers=headers)
    assert list_res.status_code == 200
    projects = list_res.json()["projects"]
    assert len(projects) == 1
    assert projects[0]["type"] == "personal"

    create_res = client.post("/api/projects", data={"name": "テストプロジェクト"}, headers=headers)
    assert create_res.status_code == 200
    project_id = create_res.json()["project_id"]

    records_res = client.get(f"/api/projects/{project_id}/records", headers=headers)
    assert records_res.status_code == 200
    assert records_res.json()["records"] == []

    invite_res = client.post(f"/api/projects/{project_id}/invites", data={"role": "editor"}, headers=headers)
    assert invite_res.status_code == 200
    assert "token" in invite_res.json()


def test_project_api_requires_login():
    anon_client = TestClient(app)
    res = anon_client.get("/api/projects")
    assert res.status_code == 401


def test_record_comments_flow():
    owner_reg = client.post("/api/auth/register", data={"username": f"cmt_owner_{uuid.uuid4().hex[:6]}", "password": "password123"})
    owner_token = owner_reg.json()["token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    member_reg = client.post("/api/auth/register", data={"username": f"cmt_member_{uuid.uuid4().hex[:6]}", "password": "password123"})
    member_token = member_reg.json()["token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    outsider_reg = client.post("/api/auth/register", data={"username": f"cmt_outsider_{uuid.uuid4().hex[:6]}", "password": "password123"})
    outsider_headers = {"Authorization": f"Bearer {outsider_reg.json()['token']}"}

    project_res = client.post("/api/projects", data={"name": "コメントテストプロジェクト"}, headers=owner_headers)
    project_id = project_res.json()["project_id"]

    invite_res = client.post(f"/api/projects/{project_id}/invites", data={"role": "editor"}, headers=owner_headers)
    invite_token = invite_res.json()["token"]
    client.post(f"/api/projects/invites/{invite_token}/accept", headers=member_headers)

    save_res = client.post(
        "/api/visibility/create",
        data={
            "user_input": "テスト請求内容",
            "request_text": "テスト請求書本文",
            "target_authority": "anjo-city",
            "visibility": "private",
            "project_id": project_id,
        },
        headers=member_headers,
    )
    assert save_res.status_code == 200
    record_id = save_res.json()["id"]

    comment_res = client.post(f"/api/records/{record_id}/comments", data={"text": "この論点を確認しましょう"}, headers=owner_headers)
    assert comment_res.status_code == 200
    assert comment_res.json()["text"] == "この論点を確認しましょう"

    list_res = client.get(f"/api/records/{record_id}/comments", headers=member_headers)
    assert list_res.status_code == 200
    comments = list_res.json()["comments"]
    assert len(comments) == 1
    assert comments[0]["username"]

    # プロジェクト外のユーザーはアクセス不可
    outsider_res = client.get(f"/api/records/{record_id}/comments", headers=outsider_headers)
    assert outsider_res.status_code == 404

    empty_comment_res = client.post(f"/api/records/{record_id}/comments", data={"text": "   "}, headers=owner_headers)
    assert empty_comment_res.status_code == 400
