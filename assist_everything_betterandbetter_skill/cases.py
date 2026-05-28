from __future__ import annotations

from dataclasses import dataclass


DIMENSIONS = {
    "reproducibility": 10,
    "memory_extraction": 20,
    "memory_application": 25,
    "update_and_decay": 20,
    "transparency": 10,
    "result_quality": 15,
}


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    domain: str
    initial_task: str
    feedback: str
    memory_query: str
    second_task: str
    preference_change: str
    third_task: str
    delete_query: str
    delete_retest_task: str
    module: str


CASES: list[EvalCase] = [
    EvalCase(
        id="C01",
        title="家庭旅行规划",
        domain="life_family_travel",
        initial_task="帮我安排北京周末 2 天亲子旅行。",
        feedback="以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。",
        memory_query="展示当前记忆。",
        second_task="帮我安排杭州 3 天家庭行程。",
        preference_change="这次父亲不去，只有我和孩子；避开网红点这条保留，但少步行不适用。",
        third_task="帮我安排上海 1 天亲子自然路线。",
        delete_query="删除孩子喜欢动物这条记忆。",
        delete_retest_task="安排南京半日游。",
        module="场景规则与条件化更新模块",
    ),
    EvalCase(
        id="C02",
        title="项目周报与决策材料",
        domain="work_report",
        initial_task="根据这些进展写一份项目周报。",
        feedback="以后写给老板的项目材料，请先给 3 条结论，再用表格列风险、负责人和下一步。",
        memory_query="展示当前记忆。",
        second_task="把这段研发进展整理成给老板看的同步材料。",
        preference_change="跨部门同步不要那么管理层风格，风险表只用于老板材料。",
        third_task="写一份给设计、研发、运营的跨部门同步。",
        delete_query="删除先给 3 条结论这条记忆。",
        delete_retest_task="再写一份老板材料。",
        module="工作流记忆与适用范围模块",
    ),
    EvalCase(
        id="C03",
        title="考试复习计划",
        domain="study_plan",
        initial_task="帮我做一个 7 天英语复习计划。",
        feedback="以后学习计划请按 25 分钟番茄钟安排；我喜欢先看例题再讲知识点；每天最后要有 5 道自测题。",
        memory_query="展示当前记忆。",
        second_task="帮我做一个 5 天高数复习计划。",
        preference_change="考试只剩两天了，番茄钟不用了，改成按高频考点冲刺；但例题先行还保留。",
        third_task="帮我做 2 天线性代数冲刺。",
        delete_query="删除每天 5 道自测题这条记忆。",
        delete_retest_task="做一个物理复习计划。",
        module="学习偏好与冲刺模式模块",
    ),
    EvalCase(
        id="C04",
        title="文献综述与研究设计",
        domain="research_review",
        initial_task="帮我整理一段关于多模态检索的文献综述。",
        feedback="以后做文献综述时，请按方法类别组织；每篇都标数据集、局限和可复现性；不要夸大结论。",
        memory_query="展示当前记忆。",
        second_task="帮我综述 RAG 评测方法。",
        preference_change="如果是头脑风暴研究问题，不要用文献综述模板；只保留谨慎表述。",
        third_task="帮我 brainstorm 3 个 RAG 研究问题。",
        delete_query="删除每篇都标可复现性这条记忆。",
        delete_retest_task="再做一段简短综述。",
        module="研究方法模板与模式切换模块",
    ),
    EvalCase(
        id="C05",
        title="恋爱礼物记忆推理",
        domain="relationship_gift",
        initial_task="帮我给女朋友选个礼物。",
        feedback="1000 左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；她已经收到过玫瑰金项链，送过的就不要再送了。",
        memory_query="展示当前记忆。",
        second_task="给我一个礼物推荐。",
        preference_change="不是，我说送过的就不要再送；如果还在同品类送应该还是玫瑰金。如果不送首饰，才能考虑紫色。能不能换个非首饰品类？",
        third_task="那给我一个非首饰推荐。",
        delete_query="删除女朋友喜欢紫色这条记忆。",
        delete_retest_task="再给一个礼物方向。",
        module="跨记忆组合与用户费力度模块",
    ),
]


def get_case(case_id: str) -> EvalCase:
    for case in CASES:
        if case.id == case_id:
            return case
    raise KeyError(case_id)
