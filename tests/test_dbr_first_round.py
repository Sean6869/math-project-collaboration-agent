"""Regression checks for the first classroom trial feedback."""
from unittest import mock
import io
from openpyxl import load_workbook
import platform_server as app
from tests.test_classroom import ClassroomTest


class DbrTest(ClassroomTest):
    def test_backfill_reopen_and_resubmit(self):
        state = app.state_get('group:1')
        state.update(preparationChecks=dict(goal=True, materials=True, rules=True))
        app.state_put('group:1', state)
        status, result = self.req(self.student, '/api/stages/submit', {'stage': 1})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['state']['stageSubmitted']['1'])
        status, result = self.req(self.student, '/api/stages/submit', {'stage': 1, 'reopen': True})
        self.assertEqual(status, 200, result)
        self.assertFalse(result['state']['stageSubmitted']['1'])
        status, result = self.req(self.student, '/api/stages/submit', {'stage': 1})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['state']['stageSubmitted']['1'])
        self.assertEqual(self.req(self.student, '/api/stages/submit', {'stage': 6})[0], 409)

    def test_quota_uses_each_level_and_roles_match_size(self):
        self.assertEqual(app.hint_policy({}), dict(L1=20, L2=10, L3=5, L4=2))
        policy=dict(L1=20,L2=10,L3=5,L4=3)
        self.assertEqual(app.hint_policy({'hintPolicy':policy,'hintPolicyVersion':2}),policy)
        self.assertEqual(set(app.roles_for_members(['资料员', '展示者'], 4)), set(app.ROLE_NAMES[:4]))
        with mock.patch.object(app, 'ark_hint', return_value=('先核对一个条件', 1, 'ok')):
            for index in range(3):
                status, result = self.req(self.student, '/api/hints', {'action':'ask', 'done':'已列出已知信息', 'issue':'检查这个条件', 'newThread':True, 'requestId':f'quota-{index}'})
                self.assertEqual(status, 200, result)
        self.assertEqual(self.state()['hintUsage']['L1'], 3)

    def test_queue_status_and_linked_export(self):
        status, result = self.req(self.student, '/api/hints', {'action':'teacher', 'issue':'检查计算', 'newThread':True, 'requestId':'teacher-help'})
        self.assertEqual(status, 200, result)
        row = self.state(self.teacher)['queue'][-1]
        result = self.command('queue', {'id':row['id'], 'handling':'viewed', 'feedback':'正在查看'})
        self.assertEqual(result[0], 200, result)
        action_state=self.state()
        action_state['workspace']['next']='核对单位'
        action_state.setdefault('events', []).append({'ts':app.utc_now(),'type':'workspace_save','requestId':row['threadId'],'stage':5,'actor':'同学甲','data':'保存工作区'})
        self.assertEqual(self.req(self.student,'/api/state',{'state':action_state},'PUT')[0],200)
        _, records = self.req(self.teacher, '/api/export?format=json')
        linked = [item for item in records if item.get('request_id') == row['threadId']]
        self.assertTrue(any(item['event_type'] == 'teacher_help_status' for item in linked))
        self.assertTrue(all(item.get('event_id') and 'ts' in item for item in records))
        self.assertEqual(len({item['event_id'] for item in records}),len(records))
        self.assertTrue(any(item['event_type']=='workspace_save' and item['request_id']==row['threadId'] for item in records))
        with self.teacher.open(self.base + '/api/export?format=xlsx') as response:
            book = load_workbook(io.BytesIO(response.read()))
        self.assertIn('字段说明', book.sheetnames)
        self.assertEqual(book['课堂过程']['A1'].value, 'event_id')
        self.assertEqual(book['课堂过程']['B1'].value, 'request_id')
        expected_request_id = row['threadId']
        self.assertTrue(any(values[1] == expected_request_id for values in book['课堂过程'].iter_rows(min_row=2, values_only=True)))

    def test_field_contract_workflow_and_reusable_templates(self):
        forms = app.classroom.task_forms
        rules = forms.clean_field_rules({
            'under-problem': {'type':'number','required':True},
            'under-known': {'required':False},
            'under-constraints': {'required':False},
        })
        config = self.command('config', {'fieldRules':rules})
        self.assertEqual(config[0], 200, config)
        state = app.state_get('group:1')
        state['workspace'] = {'problem':'0'}
        self.assertTrue(app.stage_task_status(state,rules)[1][1])
        self.assertTrue(app.stage_task_status(state,rules)[1][2])
        state['workspace']['problem']='不是数字'
        self.assertFalse(app.stage_task_status(state,rules)[1][1])
        workflow = forms.clean_workflow({'2.1': {'mode':'all','fields':['under-known']}})
        self.assertFalse(app.stage_task_status(state,rules,workflow)[1][1])
        with self.assertRaises(ValueError):
            forms.clean_workflow({'1.0':{'dependsOn':['1.1']},'1.1':{'dependsOn':['1.0']}})
        with self.assertRaises(ValueError):
            forms.clean_workflow({'1.0':{'dependsOn':['2.1']}})
        status, template = self.req(self.teacher, '/api/classroom/field-templates', {
            'name':'问题填写模板','stage':2,'task':1,'fieldRules':rules,'workflow':workflow})
        self.assertEqual(status,200,template)
        latest = template['templates'][-1]
        self.assertEqual(set(latest['fieldRules']),{'under-problem'})
        self.assertEqual(self.req(self.student,'/api/classroom/field-templates')[0],403)
        self.assertEqual(self.req(self.teacher,'/api/classroom/field-templates')[1]['templates'][-1]['id'],latest['id'])

    def test_field_versions_checks_and_next_page(self):
        state = self.state()
        state['workspace']['problem'] = '测量操场'
        state['_operator'] = '同学甲'
        status, saved = self.req(self.student,'/api/state',{'state':state},'PUT')
        self.assertEqual(status,200,saved)
        history = saved['state']['fieldHistory']
        self.assertEqual(history[-1]['actor'],'同学甲')
        self.assertEqual(history[-1]['after'],['测量操场'])
        state = app.state_get('group:1')
        state['workspace'].update(model='长度乘宽度', relation='长度乘宽度', variables='长度、宽度', constraints='同一单位')
        state['checklist']={'0':True}
        app.ensure_model_version(state, app.utc_now(), actor='同学甲')
        self.assertEqual(state['versions'][-1]['checks'], {'0':True})
        self.assertEqual(state['versions'][-1]['actor'], '同学甲')
        state['preparationChecks']=dict(goal=True, materials=True, rules=True)
        app.state_put('group:1',state)
        result = self.command('config', {'workflow':{'1.3':{'next':'stage2-understanding.html'}}})
        self.assertEqual(result[0],200,result)
        status, result = self.req(self.student,'/api/stages/submit',{'stage':1})
        self.assertEqual(status,200,result)
        self.assertEqual(result['next'],'stage2-understanding.html')
        records=self.req(self.teacher,'/api/export?format=json')[1]
        field_records=[record for record in records if record.get('event_type')=='field_revision']
        self.assertTrue(field_records)
        self.assertNotIn('同学甲',str(field_records))


def load_tests(loader, tests, pattern):
    names = [
        'test_backfill_reopen_and_resubmit',
        'test_quota_uses_each_level_and_roles_match_size',
        'test_queue_status_and_linked_export',
        'test_field_contract_workflow_and_reusable_templates',
        'test_field_versions_checks_and_next_page',
    ]
    return loader.suiteClass(DbrTest(name) for name in names)
