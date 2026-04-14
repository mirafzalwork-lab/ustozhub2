import pytest
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app import app, init_db, DB_PATH


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Use a fresh temporary database for each test."""
    db_file = str(tmp_path / "test_users.db")
    monkeypatch.setattr("app.DB_PATH", db_file)
    import app as app_module
    app_module.DB_PATH = db_file
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        yield c


def register(client, username="alice", password="secret123"):
    return client.post(
        "/register",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def login(client, username="alice", password="secret123"):
    return client.post(
        "/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


class TestRegister:
    def test_register_success(self, client):
        rv = register(client)
        assert rv.status_code == 201
        assert b"registered" in rv.data

    def test_register_duplicate_username(self, client):
        register(client)
        rv = register(client)
        assert rv.status_code == 409

    def test_register_missing_fields(self, client):
        rv = client.post(
            "/register",
            data=json.dumps({"username": ""}),
            content_type="application/json",
        )
        assert rv.status_code == 400


class TestLogin:
    def test_login_success(self, client):
        register(client)
        rv = login(client)
        assert rv.status_code == 200
        assert b"successful" in rv.data

    def test_login_wrong_password(self, client):
        register(client)
        rv = login(client, password="wrongpassword")
        assert rv.status_code == 401

    def test_login_nonexistent_user(self, client):
        rv = login(client)
        assert rv.status_code == 401

    def test_login_missing_fields(self, client):
        rv = client.post(
            "/login",
            data=json.dumps({"username": "alice"}),
            content_type="application/json",
        )
        assert rv.status_code == 400


class TestSession:
    def test_me_authenticated(self, client):
        register(client)
        login(client)
        rv = client.get("/me")
        assert rv.status_code == 200
        assert b"alice" in rv.data

    def test_me_unauthenticated(self, client):
        rv = client.get("/me")
        assert rv.status_code == 401

    def test_logout(self, client):
        register(client)
        login(client)
        rv = client.post("/logout")
        assert rv.status_code == 200
        # After logout, /me should return 401
        rv = client.get("/me")
        assert rv.status_code == 401
