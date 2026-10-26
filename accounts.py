"""Class-bound login and administrator-managed teacher accounts."""
import contextvars
from contextlib import closing
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time

CLASS_CONTEXT = contextvars.ContextVar('mpt_class_id', default='')


def cipher(app):
    from cryptography.fernet import Fernet
    path = app.DATA_DIR / 'account-encryption.key'
    configured = os.environ.get('MPT_ACCOUNT_ENCRYPTION_KEY', '').strip()
    if configured:
        return Fernet(configured.encode())
    if not path.exists():
        key = Fernet.generate_key()
        try:
            with path.open('xb') as output:
                output.write(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except FileExistsError:
            pass
    return Fernet(path.read_bytes().strip())


def initialize(app):
    with app.database() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS school_classes(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,teacher_id INTEGER NOT NULL,
              group_count INTEGER NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS class_groups(
              class_id TEXT NOT NULL,group_no INTEGER NOT NULL,code TEXT NOT NULL,
              PRIMARY KEY(class_id,group_no));
            CREATE TABLE IF NOT EXISTS project_classes(project_id TEXT PRIMARY KEY,class_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS teacher_passwords(teacher_id INTEGER PRIMARY KEY,encrypted TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS project_class_assign AFTER INSERT ON projects BEGIN
              INSERT OR IGNORE INTO project_classes(project_id,class_id)
              SELECT NEW.id,id FROM school_classes
              WHERE id=current_class() OR teacher_id=NEW.created_by OR NEW.created_by IS NULL
              ORDER BY CASE WHEN id=current_class() THEN 0 WHEN teacher_id=NEW.created_by THEN 1 ELSE 2 END,created_at,id LIMIT 1;
            END;
            CREATE TRIGGER IF NOT EXISTS project_class_delete AFTER DELETE ON projects BEGIN
              DELETE FROM project_classes WHERE project_id=OLD.id;
            END;
            CREATE TRIGGER IF NOT EXISTS teacher_password_delete AFTER DELETE ON teachers BEGIN
              DELETE FROM teacher_passwords WHERE teacher_id=OLD.id;
            END;
        ''')
        if 'class_id' not in {r[1] for r in db.execute('PRAGMA table_info(sessions)')}:
            db.execute('ALTER TABLE sessions ADD COLUMN class_id TEXT')
        teacher_columns = {r[1] for r in db.execute('PRAGMA table_info(teachers)')}
        if 'student_login_code' not in teacher_columns:
            db.execute('ALTER TABLE teachers ADD COLUMN student_login_code TEXT')
        if 'school' not in teacher_columns:
            db.execute('ALTER TABLE teachers ADD COLUMN school TEXT NOT NULL DEFAULT \'\'')
        if 'grade' not in teacher_columns:
            db.execute('ALTER TABLE teachers ADD COLUMN grade TEXT NOT NULL DEFAULT \'\'')
        teachers = db.execute('SELECT id,student_login_code FROM teachers ORDER BY id').fetchall()
        used = {str(row['student_login_code']) for row in teachers if row['student_login_code']}
        for row in teachers:
            if row['student_login_code']:
                continue
            code = _new_student_code(used)
            used.add(code)
            db.execute('UPDATE teachers SET student_login_code=? WHERE id=?', (code, row['id']))
        migrated = db.execute("SELECT 1 FROM settings WHERE key='class_accounts_v1'").fetchone()
        if not migrated:
            admin = db.execute('SELECT id FROM teachers ORDER BY is_admin DESC,id LIMIT 1').fetchone()
            if admin:
                count = db.execute("SELECT value FROM settings WHERE key='group_count'").fetchone()
                count = max(1, min(20, int(count[0]) if count else 3))
                db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)', ('default', '默认班级', admin[0], count, app.utc_now()))
                db.executemany('INSERT INTO class_groups VALUES(?,?,?)', [('default', n, f'{n:02}') for n in range(1, count+1)])
                for project in db.execute('SELECT id,created_by FROM projects').fetchall():
                    owner = project['created_by'] or admin[0]
                    row = db.execute('SELECT id FROM school_classes WHERE teacher_id=? ORDER BY created_at,id LIMIT 1', (owner,)).fetchone()
                    cid = row[0] if row else secrets.token_hex(12)
                    if not row:
                        teacher = db.execute('SELECT username FROM teachers WHERE id=?', (owner,)).fetchone()
                        db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)', (cid, (teacher[0] if teacher else '教师')+'的班级', owner, count, app.utc_now()))
                        db.executemany('INSERT INTO class_groups VALUES(?,?,?)', [(cid, n, f'{n:02}') for n in range(1,count+1)])
                    db.execute('INSERT OR REPLACE INTO project_classes VALUES(?,?)', (project['id'], cid))
                db.execute('DELETE FROM sessions')
                db.execute("INSERT INTO settings VALUES('class_accounts_v1','1')")
        # No active path may authenticate through the removed codes.
        db.execute("DELETE FROM settings WHERE key IN ('class_code_hash','admin_recovery_hash','teacher_invite_hash','setup_token_hash','group_code_prefix')")
        db.execute('DELETE FROM join_tokens')
    if app.SETUP_TOKEN_FILE.exists():
        app.SETUP_TOKEN_FILE.unlink()


def active_class(app):
    with app.database() as db:
        project = app.PROJECT_CONTEXT.get()
        if project:
            return db.execute('SELECT c.* FROM school_classes c JOIN project_classes p ON p.class_id=c.id WHERE p.project_id=?', (project,)).fetchone()
        if CLASS_CONTEXT.get():
            return db.execute('SELECT * FROM school_classes WHERE id=?', (CLASS_CONTEXT.get(),)).fetchone()
        return db.execute("SELECT * FROM school_classes WHERE id='default'").fetchone()


def classes_for(app, session=None):
    with app.database() as db:
        rows = db.execute('SELECT c.*,t.username AS teacher_name FROM school_classes c JOIN teachers t ON t.id=c.teacher_id ORDER BY c.created_at,c.id').fetchall()
    return [dict(row) for row in rows if not session or session.get('isAdmin') or row['teacher_id']==session.get('teacherId')]


def can_project(app, session, project_id):
    with app.database() as db:
        row = db.execute('SELECT c.id,c.teacher_id FROM project_classes p JOIN school_classes c ON c.id=p.class_id WHERE p.project_id=?', (project_id,)).fetchone()
    if not row:
        return False
    if session['role']=='student':
        return row['id']==session.get('classId') and row['teacher_id']==session.get('teacherId') and app.state_get_project_public(project_id)
    return bool(session.get('isAdmin') or row['teacher_id']==session.get('teacherId'))


def create_admin(app, username, password, count=3, class_name='默认班级'):
    validate_teacher(username, password)
    count = valid_count(count)
    encrypted = cipher(app).encrypt(password.encode()).decode()
    with app.database() as db:
        if db.execute('SELECT 1 FROM teachers').fetchone():
            raise ValueError('平台已初始化')
        code = _new_student_code(set())
        db.execute('INSERT INTO teachers(username,password_hash,created_at,is_admin,student_login_code,school,grade) VALUES(?,?,?,?,?,?,?)', (username.strip(), app.password_hash(password), app.utc_now(), 1, code, '', ''))
        teacher = db.execute('SELECT id FROM teachers').fetchone()[0]
        db.execute('INSERT INTO teacher_passwords VALUES(?,?)', (teacher, encrypted))
        db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)', ('default', class_name.strip() or '默认班级', teacher, count, app.utc_now()))
        db.executemany('INSERT INTO class_groups VALUES(?,?,?)', [('default', n, f'{n:02}') for n in range(1,count+1)])
        db.execute("INSERT OR REPLACE INTO settings VALUES('class_accounts_v1','1')")
    app.state_put('class', {'groupCount':count})


def bootstrap(app):
    if app.is_initialized():
        return
    username = os.environ.get('MPT_TEACHER_USERNAME','').strip()
    password = os.environ.get('MPT_TEACHER_PASSWORD','')
    if username and password:
        create_admin(app, username, password, os.environ.get('MPT_GROUP_COUNT','3'), os.environ.get('MPT_CLASS_NAME','默认班级'))
    elif os.environ.get('MPT_ENV','development')=='production':
        raise RuntimeError('首次部署请设置 MPT_TEACHER_USERNAME 和 MPT_TEACHER_PASSWORD')


def reset_on_startup(app):
    """Railway-only explicit one-shot reset, before workers or requests start."""
    marker=os.environ.get('MPT_RESET_DATA_ONCE','').strip()
    if not marker:
        return
    with app.database() as db:
        previous=db.execute("SELECT value FROM settings WHERE key='last_data_reset'").fetchone()
        admin=db.execute('SELECT id FROM teachers WHERE is_admin=1 ORDER BY id LIMIT 1').fetchone()
    if previous and previous[0]==marker:
        return
    if admin:
        reset_data(app,admin[0])
    with app.database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('last_data_reset',?)",(marker,))


def reset_data(app, admin_id):
    backup_dir=app.DATA_DIR/'backups';backup_dir.mkdir(exist_ok=True)
    stamp='before-reset-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
    destination=backup_dir/(stamp+'.db')
    with app.database() as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    # Keep uploaded originals beside the consistent SQLite snapshot. The backup
    # directory lives under the persisted data volume and is not cleared below.
    upload_backup=backup_dir/(stamp+'-uploads')
    if app.UPLOAD_DIR.exists() and any(app.UPLOAD_DIR.iterdir()):
        shutil.copytree(app.UPLOAD_DIR,upload_backup)
    with app.database() as db:
        db.execute('BEGIN IMMEDIATE')
        for table in ('sessions','project_classes','projects','states','events','ai_calls','files','lesson_jobs','course_material','course_resources','knowledge_provider_files','knowledge_chunks','classroom_requests','join_tokens','class_groups','school_classes','groups'):
            db.execute('DELETE FROM '+table)
        db.execute('DELETE FROM teachers WHERE id<>?',(admin_id,))
        db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)',('default','默认班级',admin_id,3,app.utc_now()))
        db.executemany('INSERT INTO class_groups VALUES(?,?,?)',[('default',n,f'{n:02}') for n in range(1,4)])
    if app.UPLOAD_DIR.exists():
        for path in app.UPLOAD_DIR.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    app.PROJECT_CONTEXT.set('');CLASS_CONTEXT.set('')



def validate_teacher(username, password=None):
    if not 3 <= len(str(username).strip()) <= 40:
        raise ValueError('教师账号需为3—40个字符')
    if password is not None and not 8 <= len(password) <= 128:
        raise ValueError('教师密码需为8—128位')


def valid_count(value):
    if isinstance(value,bool):
        raise ValueError('小组数量应为1—20组')
    try:
        count=int(value)
    except (ValueError,TypeError) as exc:
        raise ValueError('小组数量应为1—20组') from exc
    if not 1 <= count <= 20:
        raise ValueError('小组数量应为1—20组')
    return count


def _new_student_code(used):
    """Return a short, human-friendly teacher student-entry code."""
    for _ in range(1000):
        candidate = f'{secrets.randbelow(10000):04d}'
        if candidate not in used:
            return candidate
    raise ValueError('暂时无法生成唯一学生登录码，请稍后重试')


def register_teacher(app, body):
    username=str(body.get('username','')).strip()
    password=str(body.get('password',''))
    school=str(body.get('school','')).strip()
    grade=str(body.get('grade','')).strip()
    class_name=str(body.get('className','')).strip()
    validate_teacher(username,password)
    if not 1 <= len(school) <= 80: raise ValueError('请填写学校名称')
    if not 1 <= len(grade) <= 30: raise ValueError('请填写年级')
    if not 1 <= len(class_name) <= 60: raise ValueError('请填写班级')
    with app.database() as db:
        if db.execute('SELECT 1 FROM teachers WHERE username=?',(username,)).fetchone(): raise ValueError('教师账号已存在，请更换账号')
        used={str(r[0]) for r in db.execute('SELECT student_login_code FROM teachers').fetchall() if r[0]}
        code=_new_student_code(used)
        row=db.execute('INSERT INTO teachers(username,password_hash,created_at,is_admin,student_login_code,school,grade) VALUES(?,?,?,?,?,?,?)',(username,app.password_hash(password),app.utc_now(),0,code,school,grade))
        tid=row.lastrowid
        db.execute('INSERT INTO teacher_passwords VALUES(?,?)',(tid,cipher(app).encrypt(password.encode()).decode()))
        cid=secrets.token_hex(12)
        db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)',(cid,f'{grade} {class_name}',tid,3,app.utc_now()))
        db.executemany('INSERT INTO class_groups VALUES(?,?,?)',[(cid,n,f'{n:02}') for n in range(1,4)])
    return {'teacherId':tid,'classId':cid,'studentLoginCode':code}


def resize(app, cid, count, codes=None):
    count=valid_count(count)
    if codes is not None and (not isinstance(codes,list) or len(codes)!=count or any(not re.fullmatch(r'[A-Za-z0-9]{2,20}',str(code)) for code in codes)):
        raise ValueError('请为每组填写2—20位字母或数字小组码')
    with app.database() as db:
        current=db.execute('SELECT group_count FROM school_classes WHERE id=?',(cid,)).fetchone()
        if not current:
            raise ValueError('班级不存在')
        before={row['group_no']:row['code'] for row in db.execute('SELECT * FROM class_groups WHERE class_id=?',(cid,))}
        for number in range(1,count+1):
            code=str(codes[number-1]) if codes else before.get(number,f'{number:02}')
            db.execute('INSERT INTO class_groups VALUES(?,?,?) ON CONFLICT(class_id,group_no) DO UPDATE SET code=excluded.code',(cid,number,code))
            if before.get(number)!=code:
                db.execute("DELETE FROM sessions WHERE role='student' AND class_id=? AND group_no=?",(cid,number))
        db.execute('UPDATE school_classes SET group_count=? WHERE id=?',(count,cid))
        db.execute("DELETE FROM sessions WHERE role='student' AND class_id=? AND group_no>?",(cid,count))
    # Removed groups are inactive, their original work is retained for restoration.
    return [{'groupNo':n,'code':f'{n:02}'} for n in range(current[0]+1,count+1)]


def login(app, handler, body):
    if not app.is_initialized():
        raise ValueError('平台尚未初始化')
    role=body.get('role');client=(handler.client_address[0],role,str(body.get('classId','')),str(body.get('groupId') if role=='student' else body.get('username','')))
    with app.LOGIN_LOCK:
        failures=[stamp for stamp in app.LOGIN_FAILURES.get(client,[]) if time.time()-stamp<600]
        app.LOGIN_FAILURES[client]=failures
    if len(failures)>=10:
        handler.json_response({'error':'登录失败次数过多，请10分钟后重试'},429);return
    teacher_id=None;group=None;cid='';valid=False
    with app.database() as db:
        if role=='teacher':
            teacher=db.execute('SELECT * FROM teachers WHERE username=?',(str(body.get('username','')).strip(),)).fetchone()
            valid=bool(teacher and app.password_ok(str(body.get('password','')),teacher['password_hash']))
            if valid:
                teacher_id=teacher['id']
                rows=classes_for(app,{'teacherId':teacher_id,'isAdmin':bool(teacher['is_admin'])})
                requested=str(body.get('classId',''))
                cid=requested if any(c['id']==requested for c in rows) else rows[0]['id'] if len(rows)==1 else ''
        elif role=='student':
            student_code=str(body.get('studentCode','')).strip()
            teacher=db.execute('SELECT id FROM teachers WHERE student_login_code=?',(student_code,)).fetchone() if re.fullmatch(r'\d{4}',student_code) else None
            teacher_id=teacher['id'] if teacher else None
            rows=classes_for(app,{'teacherId':teacher_id} if teacher_id else None)
            cid=str(body.get('classId','')) or (rows[0]['id'] if len(rows)==1 else '')
            if cid and not any(row['id']==cid for row in rows):
                cid=''
            try: group=int(body.get('groupId'))
            except (ValueError,TypeError): group=0
            row=db.execute('SELECT 1 FROM class_groups g JOIN school_classes c ON c.id=g.class_id WHERE g.class_id=? AND g.group_no=? AND g.group_no<=c.group_count',(cid,group)).fetchone()
            valid=bool(teacher and row)
        if not valid:
            with app.LOGIN_LOCK:app.LOGIN_FAILURES.setdefault(client,[]).append(time.time())
            handler.json_response({'error':'学生登录码或小组选择不正确' if role=='student' else '教师账号或密码不正确'},401);return
        token=secrets.token_urlsafe(32)
        session={'role':role,'classId':cid,'teacherId':teacher_id,'isAdmin':bool(role=='teacher' and teacher['is_admin'])}
        projects=[r['id'] for r in db.execute('SELECT id FROM projects ORDER BY created_at DESC') if can_project(app,session,r['id']) and (not cid or db.execute('SELECT 1 FROM project_classes WHERE project_id=? AND class_id=?',(r['id'],cid)).fetchone())]
        selected=projects[0] if len(projects)==1 else None
        db.execute('INSERT INTO sessions(token_hash,role,teacher_id,group_no,expires_at,created_at,project_id,class_id) VALUES(?,?,?,?,?,?,?,?)',(app.token_hash(token),role,teacher_id,group,int(time.time())+app.SESSION_SECONDS,app.utc_now(),selected,cid or None))
    with app.LOGIN_LOCK:app.LOGIN_FAILURES.pop(client,None)
    CLASS_CONTEXT.set(cid);app.PROJECT_CONTEXT.set(selected or '')
    secure='; Secure' if handler.secure_cookie_enabled() else ''
    handler.json_response({'ok':True,'role':role,'groupId':group,'classId':cid,'projectId':selected,'studentLoginCode':teacher['student_login_code'] if role=='teacher' else None},cookie=f'{app.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={app.SESSION_SECONDS}{secure}')


def overview(app):
    with app.database() as db:
        teachers=[dict(row) for row in db.execute('SELECT t.id,t.username,t.is_admin,t.student_login_code,t.school,t.grade,CASE WHEN p.encrypted IS NULL THEN 0 ELSE 1 END AS passwordAvailable FROM teachers t LEFT JOIN teacher_passwords p ON p.teacher_id=t.id ORDER BY t.id')]
        classes=classes_for(app)
        for item in classes:
            item['groups']=[{'number':row['group_no'],'code':row['code']} for row in db.execute('SELECT group_no,code FROM class_groups WHERE class_id=? AND group_no<=? ORDER BY group_no',(item['id'],item['group_count']))]
            item['entryUrl']='/?class='+item['id']
    return {'teachers':teachers,'classes':classes}


def route(app, handler, method, path):
    legacy=('/api/teacher/register','/api/admin/recover','/api/setup','/api/admin/factory-reset')
    if path=='/api/teacher/register':
        if method!='POST':
            handler.json_response({'error':'仅支持注册提交'},405);return True
        try:
            result=register_teacher(app,handler.read_json())
            handler.json_response({'ok':True,**result},201)
        except (ValueError,sqlite3.IntegrityError) as error:
            handler.json_response({'error':str(error)},400)
        return True
    if path in legacy or path.startswith('/api/classroom/join'):
        # Local first-run setup only; production bootstrap comes from Railway variables.
        if path=='/api/setup' and method=='POST' and not app.is_initialized() and os.environ.get('MPT_ENV','development')!='production' and handler.client_address[0] in ('127.0.0.1','::1'):
            try:
                body=handler.read_json();create_admin(app,str(body.get('username','')),str(body.get('password','')),body.get('groupCount',3),str(body.get('className','默认班级')))
                handler.json_response({'ok':True,'groupCount':valid_count(body.get('groupCount',3))},201)
            except ValueError as error:handler.json_response({'error':str(error)},400)
        else:handler.json_response({'error':'此入口已移除，请使用账号登录'},410)
        return True
    if path not in ('/api/classes','/api/admin/access'):
        if path != '/api/student-login-code':
            return False
    try:
        if path=='/api/student-login-code':
            session=handler.require_session('teacher')
            if not session:return True
            with app.database() as db:
                row=db.execute('SELECT student_login_code FROM teachers WHERE id=?',(session['teacherId'],)).fetchone()
                if method=='GET':
                    handler.json_response({'studentLoginCode':row['student_login_code']});return True
                if method=='POST':
                    code=str(handler.read_json().get('studentLoginCode','')).strip()
                    if not re.fullmatch(r'\d{4}',code):raise ValueError('学生登录码必须是4位数字')
                    if db.execute('SELECT 1 FROM teachers WHERE student_login_code=? AND id<>?',(code,session['teacherId'])).fetchone():raise ValueError('登录码已被占用，请更换')
                    db.execute('UPDATE teachers SET student_login_code=? WHERE id=?',(code,session['teacherId']))
                    handler.json_response({'ok':True,'studentLoginCode':code});return True
        if path=='/api/classes' and method=='GET':
            session=handler.current_session();rows=classes_for(app,session if session and session['role']=='teacher' else None)
            handler.json_response({'classes':[{'id':c['id'],'name':c['name'],'groupCount':c['group_count'],'teacherName':c['teacher_name']} for c in rows]});return True
        session=handler.require_session('teacher')
        if not session:return True
        if path=='/api/classes' and method=='POST':
            cid=str(handler.read_json().get('classId',''))
            if not any(row['id']==cid for row in classes_for(app,session)):
                handler.json_response({'error':'无权进入这个班级'},403);return True
            with app.database() as db:db.execute('UPDATE sessions SET class_id=?,project_id=NULL WHERE token_hash=?',(cid,handler.session_token_hash()))
            handler.json_response({'ok':True,'classId':cid});return True
        if not session.get('isAdmin'):
            handler.json_response({'error':'只有管理员可以管理教师和班级'},403);return True
        if method=='GET':
            handler.json_response(overview(app));return True
        body=handler.read_json();action=body.get('action')
        if action=='teacher_save':
            username=str(body.get('username','')).strip();password=str(body.get('password',''));tid=int(body.get('teacherId') or 0)
            validate_teacher(username,password if password or not tid else None)
            encrypted=cipher(app).encrypt(password.encode()).decode() if password else None
            with app.database() as db:
                if tid:
                    old=db.execute('SELECT * FROM teachers WHERE id=?',(tid,)).fetchone()
                    if not old:raise ValueError('教师不存在')
                    db.execute('UPDATE teachers SET username=?,password_hash=? WHERE id=?',(username,app.password_hash(password) if password else old['password_hash'],tid))
                else:
                    code=_new_student_code({str(r[0]) for r in db.execute('SELECT student_login_code FROM teachers').fetchall() if r[0]})
                    result=db.execute('INSERT INTO teachers(username,password_hash,created_at,is_admin,student_login_code) VALUES(?,?,?,?,?)',(username,app.password_hash(password),app.utc_now(),0,code));tid=result.lastrowid
                if encrypted:db.execute('INSERT OR REPLACE INTO teacher_passwords VALUES(?,?)',(tid,encrypted))
                db.execute('DELETE FROM sessions WHERE teacher_id=? AND token_hash<>?',(tid,handler.session_token_hash()))
            handler.json_response({'ok':True,'teacherId':tid})
        elif action=='teacher_password':
            tid=int(body.get('teacherId') or 0)
            with app.database() as db:row=db.execute('SELECT encrypted FROM teacher_passwords WHERE teacher_id=?',(tid,)).fetchone()
            if not row:raise ValueError('该旧账号需先设置新密码')
            from cryptography.fernet import InvalidToken
            try:password=cipher(app).decrypt(row[0].encode()).decode()
            except InvalidToken as error:raise ValueError('密码保存密钥不匹配，请重新设置密码') from error
            handler.json_response({'password':password})
        elif action=='class_save':
            cid=str(body.get('classId') or secrets.token_hex(12));name=str(body.get('name','')).strip();tid=int(body.get('teacherId') or 0);count=valid_count(body.get('groupCount'))
            if not 1<=len(name)<=60:raise ValueError('班级名称需为1—60字')
            codes=body.get('codes')
            if codes is not None and (not isinstance(codes,list) or len(codes)!=count or any(not re.fullmatch(r'[A-Za-z0-9]{2,20}',str(code)) for code in codes)):
                raise ValueError('请为每组填写2—20位字母或数字小组码')
            with app.database() as db:
                if not db.execute('SELECT 1 FROM teachers WHERE id=?',(tid,)).fetchone():raise ValueError('请选择任课教师')
                old=db.execute('SELECT * FROM school_classes WHERE id=?',(cid,)).fetchone()
                if old:
                    db.execute('UPDATE school_classes SET name=?,teacher_id=? WHERE id=?',(name,tid,cid))
                    if old['teacher_id']!=tid:
                        db.execute("DELETE FROM sessions WHERE class_id=? AND (role='student' OR teacher_id NOT IN (SELECT id FROM teachers WHERE is_admin=1))",(cid,))
                        db.execute('UPDATE projects SET created_by=? WHERE id IN (SELECT project_id FROM project_classes WHERE class_id=?)',(tid,cid))
                else:db.execute('INSERT INTO school_classes VALUES(?,?,?,?,?)',(cid,name,tid,count,app.utc_now()))
            resize(app,cid,count,codes)
            handler.json_response({'ok':True,'classId':cid})
        else:raise ValueError('不支持的管理操作')
    except (ValueError,TypeError,sqlite3.IntegrityError) as error:
        handler.json_response({'error':'账号已存在，请更换账号' if isinstance(error,sqlite3.IntegrityError) else str(error)},400)
    return True
