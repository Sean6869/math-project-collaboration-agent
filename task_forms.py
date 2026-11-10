"""Shared student field and task-flow contracts."""
import copy
import math
import re


FIELD_CATALOG = []


def add_field(key, title, stage, task, path, help_text, kind='textarea', required=False, fixed=False):
    FIELD_CATALOG.append(dict(id=key, title=title, stage=stage, task=task,
                              path=path.split('.') if path else [], help=help_text, type=kind,
                              required=required, fixedType=fixed, order=len(FIELD_CATALOG),
                              expanded=True, maxLength=1000))


for key, title, stage, task, path, explanation, required in [
    ('under-problem', '共同要解决的问题', 2, 1, 'workspace.problem', '用一句话说清这次要解决什么。', True),
    ('under-known', '题目给出的信息', 2, 2, 'workspace.known', '留下题目中的数据和单位。', True),
    ('under-constraints', '必须满足的条件', 2, 2, 'workspace.constraints', '写下方案必须遵守的限制。', True),
    ('under-unknown', '还有哪些信息要核对', 2, 2, 'workspace.unknown', '暂时不确定的地方可以先记下来。', False),
    ('thoughtContent', '我认为要解决的问题是', 2, 0, 'firstThoughts.*', '先写下自己的想法，再与同伴交流。', True),
    ('thoughtUncertain', '我还不确定的地方是', 2, 0, 'thoughtMeta.*.uncertain', '写下你想进一步弄清的地方。', False),
    ('questionMain', '共同要解决的问题', 2, 3, 'questionTree.0', '写下全组要回答的主要问题。', True),
    ('question-child', '小问题', 2, 3, 'questionTreeChildren.*', '写一个能用计算、图或数据检查的小问题。', True),
    ('modelConditions', '必须满足的条件', 3, 0, 'workspace.constraints', '说明研究量可以取哪些值。', True),
    ('modelRelation', '数量之间的关系', 3, 1, 'workspace.relation', '用算式、图形关系或表格表示。', True),
    ('modelReason', '这次改了什么', 3, 2, 'workspace.revisionReason', '保存新版方案时写下修改原因。', False),
    ('representationMeaning', '从图表或算式里发现了什么', 3, 3, 'mathTools.representation.meaning', '写下观察或计算得到的发现。', True),
    ('representationCheckText', '怎样确认结果没算错', 3, 3, 'mathTools.representation.check', '记录你们采用的检查方法。', True),
    ('compareConditions', '共同条件', 4, 2, 'comparison.conditions', '比较时让两种方案使用相同的条件。', False),
    ('compareReason', '为什么这样选', 4, 2, 'comparison.reason', '用计算、图表或数据说明选择理由。', False),
    ('compare-claim', '方案描述', 4, 2, 'viewpoints.*.claim', '简短说明这个方案准备怎样做。', True),
    ('compare-basis', '计算结果、测量结果或数据', 4, 2, 'viewpoints.*.basis', '留下支持这个方案的根据。', True),
    ('compare-conditions', '什么情况下可以使用这个方案', 4, 2, 'viewpoints.*.conditions', '写下使用这个方案的条件。', False),
    ('compare-risk', '还需要核对什么', 4, 2, 'viewpoints.*.risk', '记录尚未确定的地方，没有可暂时跳过。', False),
    ('recordText', '它说明了什么', 4, 0, 'evidence.*.text', '用一句话说明这条记录支持什么。', False),
    ('discussionClaim', '我的看法', 4, 2, 'debate.*.claim', '说清自己的想法。', False),
    ('discussionBasis', '我的理由或根据', 4, 2, 'debate.*.basis', '用计算、图表或事实解释。', False),
    ('discussionCheck', '我希望大家一起检查的地方', 4, 2, 'debate.*.check', '指出需要一起核对的一步。', False),
    ('discussionDecision', '讨论后的决定', 4, 2, 'debate.*.decision', '简短记录讨论后决定怎样做。', False),
    ('product-problem', '项目问题', 5, 0, 'workspace.problem', '回到这次项目最初要解决的问题。', True),
    ('product-model', '数学方案', 5, 0, 'workspace.model', '使用你们最后确定的数学方案。', True),
    ('product-evidenceValidation', '用什么根据、怎样检查', 5, 0, 'product.evidenceValidation', '说明计算、图表或记录怎样支持结论。', True),
    ('product-claim', '核心结论', 5, 0, 'product.claim', '写出根据方案和记录得到的结论。', True),
    ('product-audience', '成果给谁看、谁会使用', 5, 2, 'product.audience', '写下成果的使用者。', True),
    ('product-title', '成果标题', 5, 2, 'product.title', '给成果起一个清楚的名字。', False),
    ('product-limitations', '结论适用的条件和还没有考虑的情况', 5, 2, 'product.limitations', '说明在哪些情况下能使用这个结论。', True),
    ('rehearsal-role', '展示分工', 5, 1, 'product.rehearsalRoles.*.task', '写下这位成员负责讲什么或回答什么。', False),
    ('issueProblem', '发现了什么问题', 5, 1, 'product.rehearsalIssues.*.problem', '记录上台前排练中发现的问题。', False),
    ('issueSolution', '准备怎样解决', 5, 1, 'product.rehearsalIssues.*.solution', '说明准备如何修改。', False),
    ('reflect-changed', '这次修改了什么', 5, 3, 'product.reflectionFields.changed', '写下最重要的一处修改。', True),
    ('reflect-reason', '为什么修改', 5, 3, 'product.reflectionFields.reason', '用一句话解释修改原因。', True),
    ('reflect-helps', '使用过哪些帮助', 5, 3, 'product.reflectionFields.helps', '可以写同伴、老师或迈谱的帮助。', False),
    ('reflect-rotated', '分工是否轮换', 5, 3, 'product.reflectionFields.rotated', '简短记录这次有没有调整分工。', False),
    ('reflect-checked', '哪些根据已经检查', 5, 3, 'product.reflectionFields.checked', '写下已经核对过的计算或图表。', False),
    ('reflect-next', '下次先检查什么', 5, 3, 'product.reflectionFields.next', '写下下次准备优先检查的一件事。', True),
    ('helpNextAction', '我们接下来准备做什么', 0, -1, 'workspace.next', '把收到帮助后要尝试的一步记下来。', False),
    ('roleReason', '这次不调整分工的原因', 1, 2, '', '愿意的话可以简短写下原因。', False),
]:
    add_field(key, title, stage, task, path, explanation, required=required)

for key, label in [('name', '数量'), ('symbol', '符号'), ('meaning', '表示什么'), ('unit', '单位'), ('source', '数据来自哪里')]:
    add_field('quantity-' + key, label, 3, 0, 'workspace.quantityRows.*.' + key, '填写这个数量的' + label + '。', 'text', key == 'name')
for key, label in [('problem', '共同要解决的问题'), ('known', '题目给出的信息'), ('constraints', '必须满足的条件'),
                   ('variables', '题目中会变化、需要研究的量'), ('relation', '数量之间的关系'), ('model', '目前的数学方案'),
                   ('evidence', '已经检查的根据和还需要检查的内容'), ('next', '下一步')]:
    add_field('workspace-' + key, label, 0, -1, 'workspace.' + key, '记录小组当前已经确定的内容。')
for index, label in enumerate(['字母、符号和单位都写清楚', '算式能从题目条件中找到根据', '别人能按我们的步骤重新检查',
                               '这个量可以取哪些值', '最小、最大或接近限制的情况已检查', '结论还没有考虑到哪些情况']):
    add_field('math-check-' + str(index), label, 3, 3, 'checklist.' + str(index), '检查后再勾选。', 'checkbox', False, True)
for index in range(4):
    add_field('rehearsal-check-' + str(index), '上台前检查第' + str(index + 1) + '项', 5, 1,
              'product.rehearsalChecks.' + str(index), '完成这一项检查后勾选。', 'checkbox', True, True)
for key, title in [('goal','已阅读项目目标与成果要求'), ('materials','已核对材料与设备'), ('rules','已确认课堂规则')]:
    add_field('prep-' + key, title, 1, {'goal':0,'materials':1,'rules':3}[key],
              'preparationChecks.' + key, '确认已经完成这一项后勾选。', 'checkbox', True, True)
for key, title, kind, path in [('recordType','记录类型','select','evidence.*.type'), ('recordStage','对应哪一步','select','evidence.*.stage'),
                              ('recordVersion','对应的方案版本','select','evidence.*.modelVersion'),
                              ('recordVerified','已检查','checkbox','evidence.*.verified'), ('recordInvalid','这条记录无效','checkbox','evidence.*.invalid'),
                              ('recordFile','拍照或上传内容','file','evidence.*.file'), ('modelPhoto','纸笔照片','file','workspace.modelFiles'),
                              ('productPhoto','拍照记录成果','file','product.photos'), ('issueRechecked','修改后是否检查','checkbox','product.rehearsalIssues.*.rechecked')]:
    stage = 5 if key in ('productPhoto','issueRechecked') else 3 if key == 'modelPhoto' else 4
    add_field(key,title,stage,1 if key == 'issueRechecked' else 0,path,'按实际情况填写或选择。',kind,False,True)

for field in FIELD_CATALOG:
    if field['id'] in {'under-unknown','thoughtUncertain','modelReason','reflect-helps','reflect-rotated','reflect-checked','recordInvalid','recordVersion'}:
        field['expanded'] = False
CATALOG_BY_ID = {field['id']: field for field in FIELD_CATALOG}
PAGE_PATHS = {'dashboard.html', 'workspace.html', 'help.html', 'ai.html', 'group.html', 'history.html',
              'math-tools.html', 'math-visualizations.html'}
for stage in range(1, 6):
    PAGE_PATHS.add(f'stage{stage}.html')
PAGE_PATHS.update(['stage1-materials.html','stage1-roles.html','stage2-understanding.html','stage2-question-tree.html',
                   'stage3-model.html','stage3-graph.html','stage3-check.html','stage3-compare.html',
                   'stage4-evidence.html','stage4-discussion.html','stage5-product.html','stage5-rehearsal.html','stage5-reflection.html'])
METRICS = {'evidence': ('evidence',), 'checkedEvidence': ('evidence',), 'versions': ('versions',),
           'questions': ('questionTree',), 'members': ('members',), 'discussions': ('debate',)}


def field_values(state, path):
    if not path:
        return []
    if path[0] == 'questionTreeChildren':
        return list(state.get('questionTree', [])[1:])
    values = [state]
    for key in path:
        following = []
        for value in values:
            if key == '*':
                following.extend(value if isinstance(value, list) else value.values() if isinstance(value, dict) else [])
            elif isinstance(value, dict):
                following.append(value.get(key, value.get(int(key)) if key.isdigit() else None))
            elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
                following.append(value[int(key)])
            else:
                following.append(None)
        values = following
    if path[:2] == ['product', 'reflectionFields'] and not state.get('product', {}).get('reflectionFields'):
        index = {'changed': 0, 'reason': 1, 'next': 2}.get(path[-1])
        legacy = str(state.get('product', {}).get('reflection', '')).splitlines()
        if index is not None and len(legacy) > index:
            return [legacy[index]]
    return values


def filled(value):
    return value is not None and value is not False and (bool(value.strip()) if isinstance(value, str) else bool(value) if isinstance(value, (list,dict)) else True)


def clean_field_rules(raw):
    if not isinstance(raw, dict):
        raise ValueError('字段配置格式无效。')
    clean = {}
    for key, rule in raw.items():
        spec = CATALOG_BY_ID.get(key)
        if not spec or not isinstance(rule, dict):
            raise ValueError('字段不存在。')
        kind = rule.get('type') or spec['type']
        if kind not in ('textarea','text','number','select','radio','multiselect','checkbox','file'):
            raise ValueError('字段输入类型无效。')
        if not spec['fixedType'] and kind in ('file','checkbox'):
            raise ValueError('请为文字字段选择文字、数字、单选或多选。')
        if spec['fixedType'] and kind != spec['type']:
            raise ValueError('上传、检查与关联字段需保留原输入类型。')
        clean[key] = {name: str(rule.get(name, ''))[:1000] for name in ('title','help','hint','options')}
        clean[key].update(type=kind, required=rule.get('required', spec['required']) is True,
                          expanded=rule.get('expanded', spec['expanded']) is True,
                          order=max(0,min(200,int(rule.get('order',spec['order'])))),
                          maxLength=max(1,min(4000,int(rule.get('maxLength',1000)))),
                          hintMode=rule.get('hintMode','full'))
        if clean[key]['hintMode'] not in ('full','short','none'):
            raise ValueError('帮助显示方式无效。')
    return clean


def clean_workflow(raw):
    if not isinstance(raw, dict):
        raise ValueError('任务流程配置格式无效。')
    result = {}
    for key, item in raw.items():
        if not re.fullmatch(r'[1-5]\.[0-3]', key) or not isinstance(item, dict):
            raise ValueError('任务编号无效。')
        mode = item.get('mode','default')
        if mode not in ('default','all','any','count'):
            raise ValueError('完成条件无效。')
        dependencies = list(dict.fromkeys(item.get('dependsOn', [])))
        if len(dependencies) > 20 or any(not isinstance(dep,str) or not re.fullmatch(r'[1-5]\.[0-3]',dep) for dep in dependencies):
            raise ValueError('前置任务无效。')
        if any(int(dep[0]) > int(key[0]) for dep in dependencies):
            raise ValueError('前置任务不能来自后续阶段。')
        fields = list(dict.fromkeys(item.get('fields', [])))
        if any(field not in CATALOG_BY_ID or not CATALOG_BY_ID[field]['path'] or CATALOG_BY_ID[field]['stage'] > int(key[0]) for field in fields):
            raise ValueError('完成条件包含无效字段。')
        if mode in ('all','any') and not fields:
            raise ValueError('请为完成条件选择字段。')
        metric = item.get('metric','evidence')
        if metric not in METRICS:
            raise ValueError('记录类型无效。')
        next_page = str(item.get('next',''))
        if next_page and next_page not in PAGE_PATHS:
            raise ValueError('提交后的页面无效。')
        result[key] = dict(required=item.get('required',True) is True, mode=mode, fields=fields,
                           dependsOn=dependencies, metric=metric, minimum=max(1,min(50,int(item.get('minimum',1)))), next=next_page)
    def visit(key, ancestors):
        if key in ancestors:
            raise ValueError('前置任务不能循环依赖。')
        for dependency in result.get(key,{}).get('dependsOn',[]):
            visit(dependency, ancestors | {key})
    for key in result:
        visit(key,set())
    return result


def field_complete(state, spec, rules, required=None):
    rule = {**spec, **rules.get(spec['id'], {})}
    if required is not None:
        rule['required'] = required
    values = field_values(state, spec['path'])
    if spec['id'] == 'quantity-name' and not state.get('workspace',{}).get('quantityRows'):
        values = [state.get('workspace',{}).get('variables')]
    if not values:
        return not rule['required']
    for value in values:
        if not filled(value):
            if rule['required']:
                return False
            continue
        if isinstance(value,str) and len(value) > rule.get('maxLength',4000):
            return False
        if rule['type'] == 'number':
            try:
                if not math.isfinite(float(value)):
                    return False
            except (TypeError, ValueError):
                return False
        if rule['type'] in ('select','radio','multiselect') and not spec['fixedType']:
            options = [part.strip() for part in rule.get('options','').split('|') if part.strip()]
            selected = str(value).split('、') if rule['type'] == 'multiselect' else [str(value)]
            if options and any(part not in options for part in selected):
                return False
    return True


def apply_workflow(state, statuses, config):
    rules, workflow = config.get('fieldRules',{}), config.get('workflow',{})
    result = copy.deepcopy(statuses)
    field_tasks = {
        (2,1): ['under-problem'], (2,2): ['under-known','under-constraints'],
        (3,0): ['quantity-name','modelConditions'], (3,1): ['modelRelation'],
        (4,2): ['compare-claim','compare-basis','compare-risk'],
        (5,0): ['product-problem','product-model','product-evidenceValidation','product-claim'],
        (5,2): ['product-audience','product-claim','product-limitations'],
        (5,3): ['reflect-changed','reflect-reason','reflect-next'],
    }
    for (stage,task), fields in field_tasks.items():
        if any(key in rules for key in fields):
            result[stage-1][task] = all(field_complete(state,CATALOG_BY_ID[key],rules) for key in fields)
            if stage == 4:
                result[stage-1][task] = result[stage-1][task] and len(state.get('viewpoints',[])) >= 2
    for spec in FIELD_CATALOG:
        if spec['id'] not in rules or spec['stage'] < 1 or spec['task'] < 0 or not spec['path']:
            continue
        if rules[spec['id']].get('required') is True and not field_complete(state,spec,rules):
            if spec['id'].startswith('representation') and statuses[2][3]:
                continue
            result[spec['stage']-1][spec['task']] = False
    evaluated = {}
    def evaluate(key):
        if key in evaluated:
            return evaluated[key]
        stage, task = map(int,key.split('.'))
        rule = workflow.get(key,{})
        valid = result[stage-1][task]
        selected = [CATALOG_BY_ID[field] for field in rule.get('fields',[]) if field in CATALOG_BY_ID]
        if rule.get('mode') in ('all','any'):
            checks = [field_complete(state,field,rules,required=True) for field in selected]
            valid = all(checks) if rule['mode']=='all' else any(checks)
        elif rule.get('mode') == 'count':
            metric = rule.get('metric','evidence')
            records = state.get(METRICS[metric][0],[])
            if metric == 'checkedEvidence':
                records = [row for row in records if row.get('verified') and not row.get('invalid')]
            elif metric == 'evidence':
                records = [row for row in records if not row.get('invalid')]
            elif metric == 'questions':
                records = [row for row in records[1:] if filled(row)]
            valid = len(records) >= rule.get('minimum',1)
        valid = valid and all(evaluate(dep) for dep in rule.get('dependsOn',[]))
        evaluated[key] = valid
        return valid
    for stage in range(1,6):
        for task in range(4):
            key = f'{stage}.{task}'
            result[stage-1][task] = evaluate(key) or workflow.get(key,{}).get('required') is False
    return result
