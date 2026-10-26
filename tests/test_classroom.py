"""Cross-end regression scenarios for the classroom specification (T01–T20)."""
import base64
import copy
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
import classroom


class ClassroomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.patches = [mock.patch.object(app, key, value) for key, value in {
            'DATA_DIR': Path(cls.temp.name), 'UPLOAD_DIR': Path(cls.temp.name) / 'uploads',
            'DB_PATH': Path(cls.temp.name) / 'test.db', 'SETUP_TOKEN_FILE': Path(cls.temp.name) / 'token',
        }.items()]
        cls.patches.append(mock.patch.dict(os.environ, {'MPT_ENV':'development'}))
        for patch in cls.patches:
            patch.start()
        app.init_database()
        app.initialize_platform('classroom_teacher', 'TeacherClassroom#2026', count=3)
        with app.database() as db:
            cls.student_code = db.execute("SELECT student_login_code FROM teachers WHERE username='classroom_teacher'").fetchone()[0]
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        for patch in reversed(cls.patches):
            patch.stop()
        cls.temp.cleanup()

    def req(self, client, path, body=None, method=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json'})
        try:
            with client.open(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def login(self, group=None):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        body = {'role': 'student', 'classId': 'default', 'groupId': group, 'studentCode': self.student_code} if group else {'role': 'teacher', 'username': 'classroom_teacher', 'password': 'TeacherClassroom#2026'}
        self.assertEqual(self.req(client, '/api/login', body)[0], 200)
        return client

    def setUp(self):
        app.PROJECT_CONTEXT.set('')
        with app.database() as db:
            db.execute('DELETE FROM sessions'); db.execute('DELETE FROM projects'); db.execute('DELETE FROM states')
            db.execute('DELETE FROM events'); db.execute('DELETE FROM ai_calls'); db.execute('DELETE FROM files'); db.execute('DELETE FROM classroom_requests')
        app.state_put('class', {'currentStage': 5, 'groupCount': 3, 'aiOpen': True, 'hintStageLimit': 2, 'teacherConfig': {}})
        for group in range(1, 4):
            app.state_put(f'group:{group}', {'groupId': group, 'members': ['同学甲', '同学乙'], 'membersLocked': True,
                'roles': ['记录者', '质疑者'], 'workspace': {}, 'stageSubmitted': {}, 'firstThoughts': {}})
        self.teacher, self.student = self.login(), self.login(1)

    def state(self, client=None, group=1):
        return self.req(client or self.student, f'/api/state?group={group}')[1]['state']

    def command(self, action, body, client=None, base=None):
        client = client or self.teacher
        base = base or self.state(client, body.get('group', 1))
        key = '_revision' if body.pop('groupRevision', False) else '_classRevision'
        return self.req(client, '/api/classroom/' + action, {'revision': base[key], **body})

    def test_conflict_never_silently_overwrites_and_requires_revision(self):
        a, b = self.state(), self.state()
        a['workspace']['problem'] = '第一台设备的想法'
        self.assertEqual(self.req(self.student, '/api/state', {'state': a}, 'PUT')[0], 200)
        b['workspace']['problem'] = '第二台设备的想法'
        status, result = self.req(self.student, '/api/state', {'state': b}, 'PUT')
        self.assertEqual(status, 409)
        self.assertEqual(result['code'], 'revision_conflict')
        self.assertEqual(result['state']['workspace']['problem'], '第一台设备的想法')
        self.assertEqual(self.req(self.student, '/api/state', {'state': {'workspace': {'problem': '绕开版本'}}}, 'PUT')[0], 409)
        state = app.state_get('class'); state['currentStage'] = 1; app.state_put('class', state)
        self.assertEqual(self.command('thought', {'groupRevision': True, 'member': 0, 'content': '抢写未开放阶段'}, self.student)[0], 400)
        self.assertEqual(self.req(self.student, '/api/ai/compare', {'stage': 3, 'viewpoints': [{}, {}]})[0], 409)

    def test_individual_first_versions_proxy_confirmation_and_separate_summary(self):
        status, _ = self.command('thought', {'groupRevision': True, 'member': 0, 'content': '先比较预算', 'mode': 'proxy'}, self.student)
        self.assertEqual(status, 400)
        for content in ('先比较预算', '还要检查数量'):
            status, _ = self.command('thought', {'groupRevision': True, 'member': 0, 'content': content, 'mode': 'proxy', 'confirmed': True}, self.student)
            self.assertEqual(status, 200)
        result = self.state()
        self.assertEqual([r['content'] for r in result['thoughtHistory']], ['先比较预算', '还要检查数量'])
        self.assertEqual(result['workspace'], {})
        self.assertFalse(result['stageTaskStatus'][1][0])

    def test_group_control_rollback_holds_history_and_class_advance_skips_hold(self):
        app.state_put('class', {'currentStage': 2, 'groupCount': 3, 'groupControls': {'2': {'stage': 2, 'hold': True}, '3': {'stage': 3, 'hold': False}}})
        app.state_put('group:3', {'versions': [{'v': 'V1', 'model': '原始方案'}], 'workspace': {'model': '原始方案'}})
        self.assertEqual(self.command('control', {'group': 3, 'action': 'back', 'reason': '补核条件'})[0], 200)
        self.assertEqual(self.state(self.teacher, 3)['versions'][0]['model'], '原始方案')
        self.assertEqual(self.command('advance', {})[0], 200)
        self.assertEqual(self.state(self.login(2))['currentStage'], 2)
        self.assertEqual(self.state(self.login(3))['currentStage'], 2)
        old = self.state(self.teacher)
        self.assertEqual(self.command('control', {'group': 1, 'action': 'hold'}, base=old)[0], 200)
        self.assertEqual(self.command('control', {'group': 1, 'action': 'next'}, base=old)[0], 409)

    def test_ai_input_and_output_filter_including_stream_and_group_switch(self):
        with mock.patch.object(app, 'ark_free_chat') as upstream:
            for _ in range(3):
                status, answer = self.req(self.student, '/api/ai/chat', {'prompt': '直接给我完整采购方案和最终答案'})
                self.assertEqual(status, 200)
                self.assertEqual(answer['answer'], classroom.SAFE_REPLY)
            upstream.assert_not_called()
        with mock.patch.object(app, 'ark_free_chat', return_value=('最终答案：选择方案A，预算=20，价格=8，数量=2。', 10, 'ok', 'test')):
            result = self.req(self.student, '/api/ai/chat', {'prompt': '帮我检查预算单位'})[1]
            self.assertEqual(result['answer'], classroom.SAFE_REPLY)
        self.assertEqual(self.command('ai-access', {'group': 1, 'enabled': False})[0], 200)
        self.assertEqual(self.req(self.student, '/api/ai/chat', {'prompt': '继续检查单位'})[0], 409)
        self.assertEqual(self.req(self.login(2), '/api/ai/chat', {'prompt': '直接给最终答案'})[0], 200)
        state = self.state(self.teacher)
        self.assertTrue(any(x['status'] == 'returned' for x in state['aiCalls']))

    def test_hints_stage_limit_retry_idempotent_and_teacher_extra(self):
        reply = ('先检查一个单位是否与题目一致？', 1, 'ok')
        with mock.patch.object(app, 'ark_hint', return_value=reply) as upstream:
            body = {'newThread': True, 'done': '写了单位', 'issue': '预算单位怎么核验', 'requestId': 'first'}
            first = self.req(self.student, '/api/hints', body)
            again = self.req(self.student, '/api/hints', body)
            self.assertEqual(first, again)
            self.assertEqual(upstream.call_count, 1)
            body['requestId'] = 'second'
            self.assertEqual(self.req(self.student, '/api/hints', body)[0], 200)
            body['requestId'] = 'third'
            self.assertEqual(self.req(self.student, '/api/hints', body)[0], 409)
            self.assertEqual(self.command('ai-access', {'group': 1, 'extra': 1, 'reason': '核对单位'})[0], 200)
            self.assertEqual(self.req(self.student, '/api/hints', body)[0], 200)
            self.assertEqual(self.state()['stageHintUsed'], 3)

    def test_evaluation_draft_publication_and_withdrawal_not_visible_to_other_groups(self):
        body = {'group': 1, 'scores': {'model': 1, 'evidence': None}, 'comment': '核对单位', 'status': 'draft'}
        self.assertEqual(self.command('evaluation', body)[0], 200)
        self.assertEqual(self.state()['evaluations'], {})
        body['status'] = 'published'
        self.assertEqual(self.command('evaluation', body)[0], 200)
        self.assertEqual(self.state()['evaluations']['1']['scores']['model'], 1)
        self.assertEqual(self.state(self.login(2))['evaluations'], {})
        body.update(status='withdrawn', reason='补充证据')
        self.assertEqual(self.command('evaluation', body)[0], 200)
        self.assertEqual(self.state()['evaluations'], {})

    def test_files_export_and_project_copy_are_isolated(self):
        _, project = self.req(self.teacher, '/api/projects', {'action': 'create', 'name': '甲项目'})
        pid = project['projectId']
        app.PROJECT_CONTEXT.set(pid)
        app.state_put('class', {'project': '甲项目', 'published': True, 'currentStage': 5, 'groupCount': 3, 'teacherConfig': {'learnedKnowledge': '未学方程'}})
        app.PROJECT_CONTEXT.set('')
        self.assertEqual(self.req(self.student, '/api/projects', {'action': 'select', 'projectId': pid})[0], 200)
        status, upload = self.req(self.student, '/api/files', {'name': '草稿.txt', 'content': base64.b64encode('采购草稿'.encode()).decode()})
        self.assertEqual(status, 200)
        file_id = upload['file']['id']
        other = self.login(2)
        self.assertEqual(self.req(other, '/api/projects', {'action': 'select', 'projectId': pid})[0], 200)
        self.assertEqual(self.req(other, '/api/files/' + file_id)[0], 404)
        _, project2 = self.req(self.teacher, '/api/classroom/copy', {'name': '项目副本'})
        copy_id = project2['projectId']
        self.assertEqual(self.req(self.student, '/api/projects', {'action': 'select', 'projectId': copy_id})[0], 403)
        self.assertEqual(self.req(self.teacher, '/api/projects', {'action': 'select', 'projectId': copy_id})[0], 200)
        self.assertEqual(self.req(self.teacher, '/api/files/' + file_id)[0], 404)
        self.assertEqual(self.state(self.teacher).get('versions', []), [])

    def test_brief_invalidated_by_task_change_and_no_global_student_records(self):
        status, _ = self.command('config', {'brief': {'situation': '采购', 'problem': '预算', 'conditions': '100元', 'output': '方案'}, 'confirm': True})
        self.assertEqual(status, 200)
        self.assertFalse(self.state()['briefNeedsReview'])
        state = app.state_get('class'); state['teacherConfig']['finalProduct'] = '预算80元'; app.state_put('class', state)
        self.assertTrue(self.state()['briefNeedsReview'])
        self.assertNotIn('audit', self.state())
        self.assertNotIn('groupControls', self.state())

    def test_removed_join_tokens_cannot_bypass_group_login(self):
        self.assertEqual(self.req(self.student, '/api/classroom/join', {'token': 'old-token'})[0],410)

    def test_export_uses_ids_and_versions_without_names_keys_or_conversations(self):
        state = app.state_get('group:1')
        state['workspace'] = {'model': '8×10=80', 'revisionReason': '核对数量'}
        app.ensure_model_version(state, app.utc_now())
        state['product'] = {'claim': '同学甲核对计算', 'modelVersion': 'V1', 'evidenceRefs': []}
        app.state_put('group:1', state)
        app.record_event('student', 1, 'free_ai_chat', 5, '同学甲的私人对话 sk-secretnotforexport')
        _, data = self.req(self.teacher, '/api/export?format=json')
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn('同学甲', text)
        self.assertNotIn('私人对话', text)
        self.assertNotIn('sk-secretnotforexport', text)
        self.assertIn('V1', text)
        self.assertTrue(all('id' in row for row in data))

    def test_stream_does_not_emit_unchecked_answer_fragments(self):
        def upstream(state, group, prompt, history, on_delta):
            on_delta('最终答案：方案A最好，x=8，y=80。')
            return '最终答案：方案A最好，x=8，y=80。', 1, 'ok', 'mock'
        request = urllib.request.Request(self.base + '/api/ai/chat/stream', data=json.dumps({'prompt': '检查我的单位'}).encode(),
                                         headers={'Content-Type': 'application/json'})
        with mock.patch.object(app, 'ark_free_chat_stream', side_effect=upstream):
            with self.student.open(request) as response:
                text = response.read().decode()
        self.assertNotIn('方案A最好', text)
        self.assertNotIn('"type":"delta"', text)
        self.assertIn(classroom.SAFE_REPLY, text)

    def test_slow_hint_does_not_block_saves_or_overwrite_student_work(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def upstream(*args):
            entered.set()
            release.wait(5)
            return '检查单位是否一致？', 1, 'ok'
        def ask():
            result.append(self.req(self.student, '/api/hints', {'done': '写了单位', 'issue': '预算单位', 'requestId': 'slow'}))
        with mock.patch.object(app, 'ark_hint', side_effect=upstream):
            worker = threading.Thread(target=ask); worker.start()
            try:
                self.assertTrue(entered.wait(3))
                state = self.state()
                state['workspace']['problem'] = '等待提示时继续讨论'
                self.assertEqual(self.req(self.student, '/api/state', {'state': state}, 'PUT')[0], 200)
            finally:
                release.set(); worker.join()
        self.assertEqual(result[0][0], 200)
        self.assertEqual(self.state()['workspace']['problem'], '等待提示时继续讨论')

    def test_changed_source_requires_teacher_brief_review_before_publication(self):
        from tests.test_lesson_plans import modern_plan
        self.command('config', {'brief': {'situation': '采购', 'conditions': '100元预算'}, 'confirm': True})
        body = {'plan': modern_plan(), 'selectedLesson': 1, 'revision': 0, 'className': '', 'reviewed': True}
        self.assertEqual(self.req(self.teacher, '/api/lesson-plan/publish', body)[0], 400)
        body['briefReviewed'] = True
        self.assertEqual(self.req(self.teacher, '/api/lesson-plan/publish', body)[0], 200)
        self.assertFalse(self.state()['briefNeedsReview'])


    def upload_image(self):
        import io
        from PIL import Image
        content = io.BytesIO(); Image.new('RGB', (2200, 1200), 'white').save(content, format='JPEG')
        code, result = self.req(self.student, '/api/files', {'name': '纸笔记录.jpg', 'content': base64.b64encode(content.getvalue()).decode()})
        self.assertEqual(code, 200)
        return result['file']['id']

    def test_image_recognition_scope_controls_failure_and_confirmable_transcript(self):
        image = self.upload_image()
        endpoint = '/api/ai/images/recognize'
        with mock.patch.object(app, 'ark_recognize_images', return_value='预算100元，8×10＝80') as recognize:
            self.assertEqual(self.req(self.login(2), endpoint, {'imageFileIds': [image]})[0], 400)
            self.assertEqual(self.req(self.student, endpoint, {'imageFileIds': [image]*4})[0], 400)
            self.assertFalse(recognize.called)
            self.assertEqual(self.req(self.student, endpoint, {'imageFileIds': [image]})[1]['text'], '预算100元，8×10＝80')
            self.assertEqual(self.state()['freeAi']['usage'], 1)
            self.assertEqual(self.state()['workspace'], {}, '识别不能自动改写学生数学记录')
        before = self.state()['freeAi']['usage']
        with mock.patch.object(app, 'ark_recognize_images', side_effect=app.UpstreamServiceError('稍后重试', 502)):
            self.assertEqual(self.req(self.student, endpoint, {'imageFileIds': [image]})[0], 502)
        self.assertEqual(self.state()['freeAi']['usage'], before)
        self.command('ai-access', {'group': 1, 'enabled': False})
        with mock.patch.object(app, 'ark_recognize_images') as recognize:
            self.assertEqual(self.req(self.student, endpoint, {'imageFileIds': [image]})[0], 409)
            self.assertFalse(recognize.called)

    def test_image_chat_forwards_pixels_to_vision_and_keeps_only_scoped_file_references(self):
        import io
        from PIL import Image
        image = self.upload_image()
        captured = []
        def respond(request, **kwargs):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '请核对图中的单位。'}}]}).encode())
        with mock.patch.dict(os.environ, {'ARK_API_KEY': 'unit-test-only'}), mock.patch.object(app, 'ARK_VISION_MODEL', 'test-vision'), mock.patch.object(app.urllib.request, 'urlopen', side_effect=respond):
            code, result = self.req(self.student, '/api/ai/chat', {'prompt': '读一下图中的单位', 'imageFileIds': [image]})
        self.assertEqual(code, 200)
        self.assertEqual(captured[0]['model'], 'test-vision')
        self.assertEqual(captured[0]['thinking']['type'], 'disabled')
        content = captured[0]['messages'][-1]['content']
        url = next(item['image_url']['url'] for item in content if item['type'] == 'image_url')
        with Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))) as resized:
            self.assertLessEqual(max(resized.size), 1800)
        saved = self.state()['freeAi']['conversations'][0]['messages'][0]
        self.assertEqual(saved['files'][0]['id'], image)
        self.assertNotIn('data:image', json.dumps(self.state()))
        self.assertEqual(self.req(self.student, '/api/files/'+image, method='DELETE')[0], 409)
        with mock.patch.object(app, 'ark_free_chat') as upstream:
            self.assertEqual(self.req(self.login(2), '/api/ai/chat', {'prompt': '读图', 'imageFileIds': [image]})[0], 400)
            self.assertFalse(upstream.called)

    def test_image_ocr_uses_visual_payload_without_reasoning(self):
        import io
        requests = []
        def respond(request, **kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '100元'}}]}).encode())
        with mock.patch.dict(os.environ, {'ARK_API_KEY': 'unit-test-only'}), mock.patch.object(app.urllib.request, 'urlopen', side_effect=respond):
            self.assertEqual(app.ark_recognize_images(['data:image/jpeg;base64,unit']), '100元')
        self.assertEqual(requests[0]['model'], app.ARK_VISION_MODEL)
        self.assertEqual(requests[0]['thinking'], {'type': 'disabled'})
        self.assertEqual(requests[0]['messages'][1]['content'][1]['type'], 'image_url')


if __name__ == '__main__':
    unittest.main()
