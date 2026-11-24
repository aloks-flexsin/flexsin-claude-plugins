"""Central service: task screenshots (queue item S1): stored in `task_attachment`, served to members, deleted by the
uploader or an admin, and every request capped at 4 MB."""
import io
import json

import pytest

from central import accounts, attachments, audit, cli, db

from .test_access import PW, signed_in, token
from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_tasks import START, add, clock, team  # noqa: F401  (fixtures)

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + bytes(range(64))
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
GIF = b'GIF89a' + b'\x01' * 16
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 16
MB4 = 4 * 1024 * 1024


def upload(client, number, *files):
    """POST the task's upload form with `files` as (bytes, name) pairs."""
    data = {'csrf_token': token(client, '/my'), 'file': [(io.BytesIO(b), name) for b, name in files]}
    return client.post('/projects/alpha/tasks/%d/attachments' % number, data=data, content_type='multipart/form-data')


def delete(client, number, attachment_id, **form):
    form['csrf_token'] = token(client, '/my')
    return client.post('/projects/alpha/tasks/%d/attachments/%s/delete' % (number, attachment_id), data=form)


def stored(conn, team, number):
    return attachments.for_task(conn, team['alpha']['id'], number)


def test_sniff_knows_the_four_image_types_by_their_first_bytes():
    assert [attachments.sniff(b) for b in (PNG, JPEG, GIF, b'GIF87a...', WEBP)] == \
        ['image/png', 'image/jpeg', 'image/gif', 'image/gif', 'image/webp']
    assert attachments.sniff(b'hello, not an image') is None
    assert attachments.sniff(b'RIFF\x00\x00\x00\x00WAVEfmt ') is None
    assert attachments.clean_name('../../etc/x.png') == 'x.png' and attachments.clean_name('C:\\a\\b.png') == 'b.png'


def test_a_png_is_stored_and_served_back_byte_for_byte(team, conn):
    n = add(team)
    resp = upload(team['clients']['dev'], n, (PNG, 'shot.png'))
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/projects/alpha/tasks/%d' % n)
    rows = stored(conn, team, n)
    assert [(r['filename'], r['content_type'], r['size'], r['uploader_email']) for r in rows] == \
        [('shot.png', 'image/png', len(PNG), 'dev@example.com')]
    for who in ('dev', 'viewer', 'admin'):                              # every member sees it
        got = team['clients'][who].get('/projects/alpha/tasks/%d/attachments/%s' % (n, rows[0]['id']))
        assert got.status_code == 200 and got.data == PNG
        assert got.headers['Content-Type'] == 'image/png'
        assert got.headers['X-Content-Type-Options'] == 'nosniff'
        assert got.headers['Content-Disposition'] == 'inline'
        assert 'private' in got.headers['Cache-Control']
    assert [r['detail'] for r in audit.rows(conn, 'task.attachment.add')]
    assert team['clients']['dev'].get('/projects/alpha/tasks/%d/attachments/nope' % n).status_code == 404
    assert team['clients']['dev'].get('/projects/alpha/tasks/%d/attachments/%s' % (n + 1, rows[0]['id'])).status_code == 404


def test_several_files_in_one_request_and_every_type(team, conn):
    n = add(team)
    resp = upload(team['clients']['dev'], n, (JPEG, 'a.jpg'), (GIF, 'b.gif'), (WEBP, 'c.webp'))
    assert resp.status_code == 302
    assert [r['content_type'] for r in stored(conn, team, n)] == ['image/jpeg', 'image/gif', 'image/webp']


def test_a_text_file_named_png_is_refused_with_the_reason(team, conn):
    n = add(team)
    resp = upload(team['clients']['dev'], n, (b'just some text\n', 'x.png'), (PNG, 'good.png'))
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'x.png is not a PNG, JPEG, GIF or WebP image' in page
    assert 'CSV export' in page                                         # the task page, still usable
    assert [r['filename'] for r in stored(conn, team, n)] == ['good.png']   # the good one is kept


def test_size_limit_four_mb_and_one_byte_is_refused(team, conn):
    n = add(team)
    dev = team['accounts']['dev']
    big = PNG + b'\x00' * (MB4 + 1 - len(PNG))
    with pytest.raises(attachments.AttachmentError, match='larger than 4 MB'):
        attachments.add(conn, team['alpha'], n, dev, 'big.png', big, START)
    exact = PNG + b'\x00' * (MB4 - len(PNG))
    assert attachments.add(conn, team['alpha'], n, dev, 'exact.png', exact, START)['size'] == MB4
    with pytest.raises(attachments.AttachmentError, match='empty'):
        attachments.add(conn, team['alpha'], n, dev, 'e.png', b'', START)
    with pytest.raises(attachments.AttachmentError) as e:
        attachments.add(conn, team['alpha'], n + 7, dev, 'x.png', PNG, START)
    assert e.value.status == 404


def test_a_request_over_four_mb_gets_413_naming_the_limit(app, team, conn):
    n = add(team)
    big = PNG + b'\x00' * MB4
    resp = upload(team['clients']['dev'], n, (big, 'big.png'))
    assert resp.status_code == 413 and '4 MB' in resp.get_data(as_text=True)
    assert stored(conn, team, n) == []
    assert app.config['MAX_CONTENT_LENGTH'] == MB4
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    api = c.post('/api/v1/tasks/next?project=alpha', data=json.dumps({'runner': 'x' * (MB4 + 10)}),
                 headers={'Authorization': 'Bearer ' + team['token']['dev'], 'Content-Type': 'application/json'})
    assert api.status_code == 413 and '4 MB' in api.get_json()['error']


def test_the_eleventh_is_refused(team, conn):
    n = add(team)
    for i in range(10):
        attachments.add(conn, team['alpha'], n, team['accounts']['dev'], '%d.png' % i, PNG, START)
    resp = upload(team['clients']['dev'], n, (PNG, 'eleven.png'))
    assert resp.status_code == 400 and 'already has 10 screenshots' in resp.get_data(as_text=True)
    assert len(stored(conn, team, n)) == 10


def test_viewer_sees_but_cannot_upload_and_a_stranger_gets_404(app, team, conn):
    n = add(team)
    row = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 's.png', PNG, START)
    assert upload(team['clients']['viewer'], n, (PNG, 'v.png')).status_code == 403
    assert team['clients']['viewer'].get('/projects/alpha/tasks/%d/attachments/%s' % (n, row['id'])).status_code == 200
    accounts.create(conn, 'stranger@example.com', PW)
    stranger = signed_in(app, 'stranger@example.com')
    assert stranger.get('/projects/alpha/tasks/%d/attachments/%s' % (n, row['id'])).status_code == 404
    assert upload(stranger, n, (PNG, 's.png')).status_code == 404
    assert delete(stranger, n, row['id'], confirm='1').status_code == 404
    assert upload(team['clients']['manager'], n, (PNG, 'm.png')).status_code == 302   # managers and up may
    assert len(stored(conn, team, n)) == 2


def test_upload_without_a_file_or_to_a_missing_task(team, conn):
    n = add(team)
    resp = upload(team['clients']['dev'], n)
    assert resp.status_code == 400 and 'Choose an image' in resp.get_data(as_text=True)
    assert upload(team['clients']['dev'], n + 5, (PNG, 'x.png')).status_code == 404


def test_delete_needs_confirmation_and_only_the_uploader_or_an_admin(team, conn):
    n = add(team)
    first = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'one.png', PNG, START)
    second = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'two.png', PNG, START)
    asked = delete(team['clients']['dev'], n, first['id'])
    assert asked.status_code == 200 and 'Delete the screenshot one.png?' in asked.get_data(as_text=True)
    assert len(stored(conn, team, n)) == 2                              # nothing deleted before the confirm
    for who in ('manager', 'viewer'):                                   # not theirs, not admins
        assert delete(team['clients'][who], n, first['id'], confirm='1').status_code == 403
    assert delete(team['clients']['dev'], n, first['id'], confirm='1').status_code == 302
    assert delete(team['clients']['admin'], n, second['id'], confirm='1').status_code == 302
    assert stored(conn, team, n) == []
    assert delete(team['clients']['dev'], n, first['id'], confirm='1').status_code == 404
    details = [r['detail'] for r in audit.rows(conn, 'task.attachment.delete')]
    assert len(details) == 2 and 'one.png' in json.dumps(details)
    with pytest.raises(attachments.AttachmentError) as e:
        third = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'three.png', PNG, START)
        attachments.delete(conn, team['alpha'], n, third['id'], team['accounts']['viewer'])
    assert e.value.status == 403


def test_the_task_activity_names_screenshots(team, conn):
    n = add(team)
    upload(team['clients']['dev'], n, (PNG, 'shot.png'))
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert 'added a screenshot to this task' in page


def task_page(client, number):
    return client.get('/projects/alpha/tasks/%d' % number).get_data(as_text=True)


def test_the_task_page_shows_thumbnails_linking_to_the_full_image(team, conn):
    n = add(team)
    assert 'No screenshots yet.' in task_page(team['clients']['dev'], n)
    row = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'login <bug>.png', PNG, START)
    src = '/projects/alpha/tasks/%d/attachments/%s' % (n, row['id'])
    for who in ('dev', 'viewer', 'admin'):
        page = task_page(team['clients'][who], n)
        assert '<h3>Screenshots' in page
        assert '<img src="%s" alt="login &lt;bug&gt;.png"' % src in page        # the thumbnail is the image, escaped
        assert '<a href="%s" class="shot-link"' % src in page                  # linking to the full image
        assert 'login &lt;bug&gt;.png' in page and '<bug>' not in page
        assert 'class="shot-name"' in page and 'Dev · ' in page.split('class="shot-name"', 1)[1][:400]


def test_viewers_see_no_upload_form_and_members_see_the_limits(team, conn):
    n = add(team)
    page = task_page(team['clients']['dev'], n)
    form = page.split('class="shot-upload"', 1)[0].rsplit('<form', 1)[1] + page.split('class="shot-upload"', 1)[1]
    assert 'enctype="multipart/form-data"' in form
    assert 'accept="image/png,image/jpeg,image/gif,image/webp"' in form and ' multiple' in form
    assert 'at most 4 MB per upload' in page and '10 screenshots per task' in page
    viewer = task_page(team['clients']['viewer'], n)
    assert 'shot-upload' not in viewer and 'name="file"' not in viewer


def test_the_delete_button_is_for_the_uploader_and_admins_only(team, conn):
    n = add(team)
    row = attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'one.png', PNG, START)
    action = '/projects/alpha/tasks/%d/attachments/%s/delete' % (n, row['id'])
    for who, sees in (('dev', True), ('admin', True), ('manager', False), ('viewer', False)):
        page = task_page(team['clients'][who], n)
        assert (action in page) is sees, who
        assert ('aria-label="More for one.png">⋯' in page) is sees, who
    assert 'data-confirm="Delete the screenshot one.png?"' in task_page(team['clients']['dev'], n)


def test_a_refused_upload_names_the_reason_and_the_page_still_works(team, conn):
    n = add(team)
    attachments.add(conn, team['alpha'], n, team['accounts']['dev'], 'kept.png', PNG, START)
    resp = upload(team['clients']['dev'], n, (b'nope', 'notes.png'))
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'role="alert"' in page and 'notes.png is not a PNG' in page
    assert 'kept.png' in page and 'class="shot-upload"' in page          # the section and the form are still there


def test_init_db_adds_the_table_to_an_existing_database(db_url):
    db.init_db(db_url)
    conn = db.connect(db_url)
    conn.execute("DROP TABLE task_attachment")                          # a database from before S1
    conn.execute("INSERT INTO account (id, email, password_hash, role, created_at) VALUES ('a1', 'k@example.com', 'x',"
                 " 'admin', '2026-11-01T00:00:00Z')")
    conn.commit()
    conn.close()
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(['init-db'], environ={'DATABASE_URL': db_url}, out=out, err=err) == 0
    conn = db.connect(db_url)
    try:
        assert 'data' in conn.columns('task_attachment')
        assert conn.query_one("SELECT email FROM account WHERE id = 'a1'")['email'] == 'k@example.com'
    finally:
        conn.close()
