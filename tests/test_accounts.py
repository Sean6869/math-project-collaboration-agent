"""Authentication, class ownership, migration and deployment reset contracts."""
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import platform_server as app
import accounts


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[mock.patch.object(app,key,value) for key,value in {
            'DATA_DIR':Path(self.tmp.name),'UPLOAD_DIR':Path(self.tmp.name)/'uploads',
            'DB_PATH':Path(self.tmp.name)/'test.db','SETUP_TOKEN_FILE':Path(self.tmp.name)/'setup-token',
        }.items()]
        self.patches.append(mock.patch.dict(os.environ,{'MPT_ENV':'development','MPT_TEACHER_USERNAME':'','MPT_TEACHER_PASSWORD':'','MPT_RESET_DATA_ONCE':'','MPT_ACCOUNT_ENCRYPTION_KEY':''}))
        for patch in self.patches:patch.start()
        app.PROJECT_CONTEXT.set('');accounts.CLASS_CONTEXT.set('');app.init_database()
        app.initialize_platform('admin_teacher','AdminPassword#2026',count=3)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.admin=self.client();self.assertEqual(self.req(self.admin,'/api/login',{'role':'teacher','username':'admin_teacher','password':'AdminPassword#2026'})[0],200)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for patch in reversed(self.patches):patch.stop()
        app.PROJECT_CONTEXT.set('');accounts.CLASS_CONTEXT.set('');self.tmp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def req(self,client,path,body=None):
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        try:
            with client.open(request,timeout=15) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())

    def manage(self,body):
        status,data=self.req(self.admin,'/api/admin/access',body);self.assertEqual(status,200,data);return data

    def teacher_class(self,name='二班',count=3):
        tid=self.manage({'action':'teacher_save','username':'second_teacher','password':'SecondPassword#2026'})['teacherId']
        cid=self.manage({'action':'class_save','name':name,'teacherId':tid,'groupCount':count})['classId']
        teacher=self.client();self.assertEqual(self.req(teacher,'/api/login',{'role':'teacher','username':'second_teacher','password':'SecondPassword#2026'})[0],200)
        return tid,cid,teacher

    def student(self,cid='default',group=1,code='01'):
        client=self.client();self.assertEqual(self.req(client,'/api/login',{'role':'student','classId':cid,'groupId':group,'groupCode':code})[0],200);return client

    def publish(self,pid,text):
        token=app.PROJECT_CONTEXT.set(pid)
        state=app.state_get('class');state.update(published=True,currentStage=5);app.state_put('class',state)
        app.state_put('group:1',{'workspace':{'problem':text}});app.PROJECT_CONTEXT.reset(token)

    def test_two_field_login_and_class_context_are_required_when_ambiguous(self):
        first=self.student();self.assertEqual(self.req(first,'/api/session')[1]['classId'],'default')
        _,cid,_=self.teacher_class()
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'student','groupId':1,'groupCode':'01'})[0],401)
        second=self.student(cid);self.assertEqual(self.req(second,'/api/session')[1]['classId'],cid)
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'student','classId':cid,'groupId':2,'groupCode':'01'})[0],401)
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'student','classId':'missing','groupId':1,'groupCode':'01'})[0],401)

    def test_teacher_and_student_project_and_group_data_are_bound_to_class(self):
        _,cid,teacher=self.teacher_class()
        pid1=self.req(self.admin,'/api/projects',{'action':'create','name':'一班采购'})[1]['projectId']
        pid2=self.req(teacher,'/api/projects',{'action':'create','name':'二班采购'})[1]['projectId']
        self.publish(pid1,'一班自己的记录');self.publish(pid2,'二班自己的记录')
        first=self.student();second=self.student(cid)
        self.assertEqual([p['id'] for p in self.req(first,'/api/projects')[1]['projects']],[pid1])
        self.assertEqual([p['id'] for p in self.req(second,'/api/projects')[1]['projects']],[pid2])
        self.assertEqual(self.req(first,'/api/projects',{'action':'select','projectId':pid2})[0],403)
        self.assertEqual(self.req(teacher,'/api/projects',{'action':'select','projectId':pid1})[0],403)
        self.assertEqual(self.req(first,'/api/state')[1]['state']['workspace']['problem'],'一班自己的记录')
        self.assertEqual(self.req(second,'/api/state')[1]['state']['workspace']['problem'],'二班自己的记录')
        self.assertEqual(self.req(second,'/api/state')[1]['state']['className'],'二班')
        self.assertEqual(self.req(teacher,'/api/admin/access')[0],403)
        self.assertEqual(self.req(second,'/api/admin/access')[0],403)

    def test_password_view_is_admin_only_encrypted_and_never_in_overview(self):
        tid,_,teacher=self.teacher_class()
        overview=self.req(self.admin,'/api/admin/access')[1]
        self.assertNotIn('SecondPassword#2026',json.dumps(overview))
        self.assertEqual(self.manage({'action':'teacher_password','teacherId':tid})['password'],'SecondPassword#2026')
        self.assertEqual(self.req(teacher,'/api/admin/access',{'action':'teacher_password','teacherId':tid})[0],403)
        with app.database() as db:
            encrypted=db.execute('SELECT encrypted FROM teacher_passwords WHERE teacher_id=?',(tid,)).fetchone()[0]
        self.assertNotIn('SecondPassword',encrypted)
        self.manage({'action':'teacher_save','teacherId':tid,'username':'second_teacher','password':'NewPassword#2026'})
        self.assertFalse(self.req(teacher,'/api/session')[1]['authenticated'])
        self.assertEqual(self.manage({'action':'teacher_password','teacherId':tid})['password'],'NewPassword#2026')

    def test_group_changes_are_class_local_and_shrink_preserves_original_work(self):
        tid,cid,teacher=self.teacher_class(count=4)
        other=self.student('default');student=self.student(cid,4,'04')
        pid=self.req(teacher,'/api/projects',{'action':'create','name':'班级项目'})[1]['projectId']
        token=app.PROJECT_CONTEXT.set(pid);app.state_put('group:4',{'workspace':{'problem':'第4组原始记录'}});app.PROJECT_CONTEXT.reset(token)
        self.manage({'action':'class_save','classId':cid,'name':'二班','teacherId':tid,'groupCount':2,'codes':['11','22']})
        self.assertFalse(self.req(student,'/api/session')[1]['authenticated'])
        self.assertTrue(self.req(other,'/api/session')[1]['authenticated'])
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'student','classId':cid,'groupId':1,'groupCode':'01'})[0],401)
        self.student(cid,1,'11')
        self.manage({'action':'class_save','classId':cid,'name':'二班','teacherId':tid,'groupCount':4,'codes':['11','22','03','04']})
        token=app.PROJECT_CONTEXT.set(pid);self.assertEqual(app.state_get('group:4')['workspace']['problem'],'第4组原始记录');app.PROJECT_CONTEXT.reset(token)

    def test_railway_reset_is_once_backed_up_and_keeps_only_admin_identity(self):
        self.teacher_class();self.req(self.admin,'/api/projects',{'action':'create','name':'即将清空'})
        with mock.patch.dict(os.environ,{'MPT_RESET_DATA_ONCE':'acceptance-1'}):
            accounts.reset_on_startup(app)
            with app.database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM teachers').fetchone()[0],1)
                db.execute('INSERT INTO projects VALUES(?,?,?,?)',('retained','清空后新项目',app.utc_now(),1))
            accounts.reset_on_startup(app)
            with app.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0],1)
        self.assertEqual(len(list((app.DATA_DIR/'backups').glob('*.db'))),1)
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'teacher','username':'admin_teacher','password':'AdminPassword#2026'})[0],200)

    def test_old_codes_and_join_tokens_are_not_authentication_paths(self):
        for path in ('/api/admin/recover','/api/teacher/register','/api/classroom/join','/api/classroom/join-token','/api/admin/factory-reset'):
            self.assertEqual(self.req(self.client(),path,{})[0],410)
        self.assertEqual(self.req(self.admin,'/api/admin/access',{'action':'teacher_invite'})[0],400)
        self.assertEqual(self.req(self.client(),'/api/login',{'role':'student','groupId':1,'classCode':'OLD','groupCode':'TEAM01'})[0],401)


if __name__=='__main__':unittest.main()
