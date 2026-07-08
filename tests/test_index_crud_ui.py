"""
test_index_crud_ui.py - index.html 项目 CRUD UI 渲染测试 (v1.3 M7)

检查模板渲染包含 CRUD UI 元素:
  - "+ 新建书籍" 按钮
  - 新建/编辑模态框
  - 删除确认模态框
  - 每张卡片有 编辑/删除 按钮
"""
import pytest

from review_ui import app as review_app


@pytest.fixture
def auth_disabled(monkeypatch):
    review_app._get_auth = lambda: {"enabled": False, "user": "", "password": ""}


@pytest.fixture
def client(auth_disabled):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-index-crud"
    with review_app.app.test_client() as c:
        yield c


def test_index_renders_new_button(client, tmp_projects_root):
    r = client.get("/")
    assert r.status_code == 200
    assert b"\xe6\x96\xb0\xe5\xbb\xba\xe4\xb9\xa6\xe7\xb1\x8d" in r.data or b"+ \xe6\x96\xb0\xe5\xbb\xba\xe4\xb9\xa6\xe7\xb1\x8d" in r.data
    assert b'btn-new-book' in r.data


def test_index_renders_book_modal(client, tmp_projects_root):
    r = client.get("/")
    assert b'book-modal' in r.data
    assert b'book-form' in r.data
    assert b'modal-submit' in r.data


def test_index_renders_delete_modal(client, tmp_projects_root):
    r = client.get("/")
    assert b'delete-modal' in r.data
    assert b'delete-confirm' in r.data


def test_index_renders_card_action_buttons(client, tmp_projects_root):
    r = client.get("/")
    # test_book fixture 项目应该渲染卡片 + 操作按钮
    assert b'test_book' in r.data
    assert b'data-act="edit"' in r.data
    assert b'data-act="delete"' in r.data
    # 至少一张卡片
    assert b'book-card-wrap' in r.data


def test_index_renders_form_fields(client, tmp_projects_root):
    r = client.get("/")
    # 表单字段都要在
    for field in [b'name="name"', b'name="book_name"', b'name="genre"',
                  b'name="tone"', b'name="protagonist"', b'name="antagonist"',
                  b'name="main_plot"', b'name="style"',
                  b'name="target_chapters"', b'name="words_per_chapter"',
                  b'name="llm_model"', b'name="llm_provider"', b'name="api_base"']:
        assert field in r.data, f"missing form field: {field.decode()}"


def test_index_renders_crud_javascript(client, tmp_projects_root):
    r = client.get("/")
    # 关键 JS 调用
    assert b"/api/projects" in r.data
    assert b'PUT' in r.data  # PUT method in fetch
    assert b'DELETE' in r.data  # DELETE method in fetch
    assert b'mode ===' in r.data or b"mode ==" in r.data  # mode === 'edit' check