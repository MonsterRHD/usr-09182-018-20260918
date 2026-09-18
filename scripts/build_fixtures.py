"""生成验收场景事件流并写入 fixtures/events.json。

场景一（验收示范，ROBO-2026-007 机器人谐波减速器）：
  阶段一设计定型 → 付款（后遇证据更正，付款快照冻结不变）；
  阶段二工程样机：合金钢柔轮路线试验失败 → 失败留痕 → 外部评审 →
  切换碳纤维复合材料柔轮路线 → 预算重估 → 新证据达标 → 付款 → 验收通过。
场景二（DRV-2026-011 伺服驱动器）：项目暂停，设备与未用资金处置义务保留。

运行：python3 scripts/build_fixtures.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.event_store import EventStore
from app.service import (
    Actor,
    ProjectService,
    APPLICANT,
    TECH_REVIEWER,
    EXTERNAL_EXPERT,
    APPROVER,
    FINANCE,
    FUNDER,
)
from app import projections

TZ = "+08:00"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(f"{value}{TZ}")


# -- 角色与自然人 -------------------------------------------------------------
zhang = Actor("A-zhang", APPLICANT, org="启辰机器人有限公司")          # 承担方/证据提交人
li = Actor("T-li", TECH_REVIEWER, org="资助机构技术核验中心")          # 技术核验人
wang = Actor("E-wang", EXTERNAL_EXPERT, org="中科院宁波材料所")        # 外部评审专家
zhao = Actor("F-zhao", FUNDER, org="省科技厅")                        # 资助机构（暂停/批准变更/验收）
qian = Actor("AP-qian", APPROVER, org="省科技厅资金处")                # 付款批准人
sun = Actor("FN-sun", FINANCE, org="省科技厅财务结算中心")             # 出纳执行

zhou = Actor("A-zhou", APPLICANT, org="云驱电控科技有限公司")          # 项目二承担方


def build() -> EventStore:
    store = EventStore()
    svc = ProjectService(store)

    # =====================================================================
    # 项目一：机器人高精度谐波减速器
    # =====================================================================
    svc.register_project(
        zhang,
        project_code="ROBO-2026-007",
        title="机器人高精度谐波减速器工程化",
        research_goal="研制疲劳寿命≥10000h、传动误差≤1弧分的机器人谐波减速器",
        applicant_name="启辰机器人有限公司",
        total_budget=3_600_000,
        occurred_at=dt("2026-01-08T09:00:00"),
        ip_ownership="前景知识产权由承担方所有，资助方享有免费实施许可",
    )

    svc.establish_budget(
        zhang,
        project_code="ROBO-2026-007",
        items=[
            {"item_id": "B-EQ", "name": "加工与检测设备购置费", "category": "设备费", "amount": 1_500_000},
            {"item_id": "B-MAT", "name": "样机材料费", "category": "材料费", "amount": 800_000},
            {"item_id": "B-TEST", "name": "测试化验加工费", "category": "测试费", "amount": 700_000},
            {"item_id": "B-IP", "name": "知识产权事务费", "category": "知识产权费", "amount": 400_000},
            {"item_id": "B-COOP", "name": "外部协作与评审费", "category": "协作费", "amount": 200_000},
        ],
        basis="立项预算评审意见（预算版本1）",
        occurred_at=dt("2026-01-08T09:30:00"),
    )

    # ---- 里程碑 MS-1：方案设计与仿真（阶段一，正常达标付款）----------------
    svc.propose_milestone(
        zhang,
        milestone_id="MS-1",
        project_code="ROBO-2026-007",
        stage_index=1,
        name="方案设计与仿真验证",
        objective="冻结减速器总体方案，仿真证明新齿形具备长寿命潜力",
        acceptance_metrics=[{"name": "仿真疲劳寿命_万次", "operator": ">=", "target": 10000}],
        evidence_requirements=[
            {"code": "R1", "name": "设计方案与仿真报告", "kind": "simulation", "required": True},
        ],
        linked_budget_items=["B-TEST", "B-COOP"],
        amount=500_000,
        due_date="2026-03-31",
        occurred_at=dt("2026-01-10T10:00:00"),
    )
    svc.commit_milestone(zhao, milestone_id="MS-1", occurred_at=dt("2026-01-15T14:00:00"))

    svc.submit_evidence(
        zhang,
        evidence_id="EV-SIM-1",
        milestone_id="MS-1",
        requirement_code="R1",
        kind="simulation",
        title="谐波啮合仿真报告（初版）",
        result="仿真疲劳寿命 12500 万次，满足指标",
        data_ref="s3://rd/robo007/sim-report-v1.pdf",
        occurred_at=dt("2026-03-05T11:00:00"),
    )
    svc.verify_evidence(
        li,
        evidence_id="EV-SIM-1",
        accepted=True,
        review_note="模型边界条件与台架工况一致，复核通过",
        occurred_at=dt("2026-03-09T15:00:00"),
    )
    # 第一笔拨款：批准时点冻结证据快照
    svc.approve_payment(
        qian,
        payment_id="PAY-2026-001",
        milestone_id="MS-1",
        amount=500_000,
        occurred_at=dt("2026-03-12T09:30:00"),
    )
    svc.execute_payment(
        sun,
        payment_id="PAY-2026-001",
        payee="启辰机器人有限公司",
        voucher_ref="银付字第2026031208号",
        occurred_at=dt("2026-03-13T10:00:00"),
    )

    # ---- 里程碑 MS-2：工程样机（中途切换技术路线）--------------------------
    svc.propose_milestone(
        zhang,
        milestone_id="MS-2",
        project_code="ROBO-2026-007",
        stage_index=2,
        name="减速器工程样机与台架达标",
        objective="交付工程样机并通过疲劳、精度、效率三项台架考核",
        acceptance_metrics=[
            {"name": "疲劳寿命_h", "operator": ">=", "target": 10000},
            {"name": "传动误差_arcmin", "operator": "<=", "target": 1.0},
            {"name": "传动效率", "operator": ">=", "target": 0.85},
        ],
        evidence_requirements=[
            {"code": "R1", "name": "疲劳台架试验报告", "kind": "test_result", "required": True},
            {"code": "R2", "name": "传动精度检测报告", "kind": "inspection", "required": True},
            {"code": "R3", "name": "核心结构专利受理", "kind": "patent", "required": True},
        ],
        linked_budget_items=["B-EQ", "B-MAT"],
        amount=2_100_000,
        due_date="2026-08-31",
        route="合金钢柔轮+传统热处理",
        occurred_at=dt("2026-01-10T10:20:00"),
    )
    # 阶段承诺可协商：资助机构压减金额后承诺生效（形成修订版本）
    svc.negotiate_milestone(
        zhao,
        milestone_id="MS-2",
        changes={"amount": 2_000_000},
        negotiated_by=[{"actor_id": "A-zhang", "role": "applicant"}, {"actor_id": "F-zhao", "role": "funder"}],
        occurred_at=dt("2026-01-14T09:00:00"),
    )
    svc.commit_milestone(zhao, milestone_id="MS-2", occurred_at=dt("2026-01-15T14:10:00"))

    # 路线 A 试验失败——失败必须留痕，价值保留
    svc.record_trial_failure(
        zhang,
        evidence_id="EV-FAIL-1",
        milestone_id="MS-2",
        trial_name="合金钢柔轮疲劳台架（第一轮）",
        hypothesis="经传统热处理的合金钢柔轮可达到10000h疲劳寿命",
        observed="6200h 时柔轮杯底出现贯穿性裂纹，台架中止",
        root_cause="热处理残余应力在齿根集中，诱发疲劳裂纹",
        learned_value="排除了合金钢+传统热处理路线；残余应力分布数据为复合材料柔轮铺层设计提供直接依据",
        data_ref="s3://rd/robo007/trial-fail-0612.pdf",
        occurred_at=dt("2026-04-18T17:20:00"),
    )

    # 承担方基于失败证据申请切换技术路线
    svc.request_route_switch(
        zhang,
        change_id="CHG-2026-002",
        milestone_id="MS-2",
        from_route="合金钢柔轮+传统热处理",
        to_route="碳纤维复合材料柔轮+齿形修形",
        reason="第一轮台架失败证明金属路线残余应力难题短期不可解；团队已有复合材料铺层工艺基础",
        failure_evidence_ids=["EV-FAIL-1"],
        occurred_at=dt("2026-04-22T09:00:00"),
    )

    # 外部独立评审：失败鉴定 + 路线意见
    svc.record_external_review(
        wang,
        review_id="REV-2026-005",
        milestone_id="MS-2",
        scope="route_switch",
        verdict="通过：失败数据可信，同意切换至碳纤维复合材料柔轮路线",
        recommendation="新路线须补做3批复合材料疲劳试验；原金属路线知识产权归属不变",
        data_ref="s3://review/2026/005-route-review.pdf",
        occurred_at=dt("2026-05-06T13:30:00"),
    )

    # 资助机构批准切换：新承诺版本 + 预算重估（旧预算版本保留）
    svc.approve_route_switch(
        zhao,
        change_id="CHG-2026-002",
        review_id="REV-2026-005",
        new_requirements=[
            {"code": "R1", "name": "复合材料柔轮疲劳台架报告", "kind": "test_result", "required": True},
            {"code": "R2", "name": "传动精度检测报告", "kind": "inspection", "required": True},
            {"code": "R3", "name": "复合材料柔轮专利受理", "kind": "patent", "required": True},
            {"code": "R4", "name": "失败分析与路线对比报告", "kind": "failure_log", "required": True},
        ],
        budget_reestimate=[
            {"item_id": "B-MAT", "old_amount": 800_000, "new_amount": 650_000, "reason": "复合材料近净成形，机加材料损耗下降"},
            {"item_id": "B-TEST", "old_amount": 700_000, "new_amount": 950_000, "reason": "新增3批复合材料疲劳试验与第三方见证"},
        ],
        new_amount=1_700_000,
        new_linked_budget_items=["B-EQ", "B-MAT", "B-TEST"],
        conditions=["切换后首批疲劳试验须第三方见证", "里程碑尾款与验收结论挂钩"],
        new_budget_items=[
            {"item_id": "B-EQ", "name": "加工与检测设备购置费", "category": "设备费", "amount": 1_500_000},
            {"item_id": "B-MAT", "name": "样机材料费（复合材料）", "category": "材料费", "amount": 650_000},
            {"item_id": "B-TEST", "name": "测试化验加工费", "category": "测试费", "amount": 950_000},
            {"item_id": "B-IP", "name": "知识产权事务费", "category": "知识产权费", "amount": 400_000},
            {"item_id": "B-COOP", "name": "外部协作与评审费", "category": "协作费", "amount": 100_000},
        ],
        occurred_at=dt("2026-05-12T10:00:00"),
    )

    # 新路线证据（针对新承诺版本提交；旧路线失败证据仍有效并满足 R4）
    svc.submit_evidence(
        zhang,
        evidence_id="EV-FATIGUE-2",
        milestone_id="MS-2",
        requirement_code="R1",
        kind="test_result",
        title="复合材料柔轮疲劳台架报告（第三方见证）",
        result="三组试样平均寿命 12100h，通过",
        data_ref="s3://rd/robo007/fatigue-v2.pdf",
        occurred_at=dt("2026-07-15T16:00:00"),
    )
    svc.verify_evidence(
        li, evidence_id="EV-FATIGUE-2", accepted=True,
        review_note="第三方见证签章齐全，原始曲线可追溯",
        occurred_at=dt("2026-07-19T10:00:00"),
    )
    svc.submit_evidence(
        zhang,
        evidence_id="EV-PREC-2",
        milestone_id="MS-2",
        requirement_code="R2",
        kind="inspection",
        title="传动精度检测报告",
        result="传动误差 0.6 弧分，传动效率 91%，通过",
        data_ref="s3://rd/robo007/precision-v2.pdf",
        occurred_at=dt("2026-07-22T10:30:00"),
    )
    svc.verify_evidence(
        li, evidence_id="EV-PREC-2", accepted=True,
        review_note="计量院复检数据一致",
        occurred_at=dt("2026-07-26T14:00:00"),
    )
    svc.submit_evidence(
        zhang,
        evidence_id="EV-PAT-2",
        milestone_id="MS-2",
        requirement_code="R3",
        kind="patent",
        title="发明专利《一种复合材料谐波柔轮结构》受理通知书",
        result="已受理，申请号 CN202610xxxxxx.x",
        data_ref="s3://rd/robo007/patent-accept.pdf",
        ip_ownership="承担方所有，资助方享有免费实施许可（按项目约定）",
        occurred_at=dt("2026-08-02T09:00:00"),
    )
    svc.verify_evidence(
        li, evidence_id="EV-PAT-2", accepted=True,
        review_note="专利权属与项目约定一致",
        occurred_at=dt("2026-08-05T11:00:00"),
    )
    # 失败记录本身也是要核验的有效证据
    svc.verify_evidence(
        li, evidence_id="EV-FAIL-1", accepted=True,
        review_note="失败现象、数据与结论完整，作为路线决策依据有效",
        occurred_at=dt("2026-08-06T09:30:00"),
    )

    # 第二笔拨款：快照绑定新承诺版本（rev3）下的完整证据包，含失败留痕
    svc.approve_payment(
        qian,
        payment_id="PAY-2026-014",
        milestone_id="MS-2",
        amount=1_700_000,
        occurred_at=dt("2026-08-10T09:00:00"),
    )
    svc.execute_payment(
        sun,
        payment_id="PAY-2026-014",
        payee="启辰机器人有限公司",
        voucher_ref="银付字第2026081003号",
        occurred_at=dt("2026-08-11T10:30:00"),
    )

    # 阶段一仿真报告事后发现数据挂载错误：更正不覆盖原始事实，旧付款快照不变
    svc.correct_evidence(
        zhang,
        evidence_id="EV-SIM-2",
        corrects_evidence_id="EV-SIM-1",
        reason="初版报告引用了旧网格的结果文件，更换为收敛网格数据",
        new_data_ref="s3://rd/robo007/sim-report-v2.pdf",
        result="仿真疲劳寿命 12180 万次，满足指标",
        occurred_at=dt("2026-08-18T14:00:00"),
    )
    svc.verify_evidence(
        li, evidence_id="EV-SIM-2", accepted=True,
        review_note="更正后数据复核通过；原件留存",
        occurred_at=dt("2026-08-20T10:00:00"),
    )

    # 阶段一验收（未切换路线的普通验收——不应被验收选取规则选中）
    svc.record_external_review(
        wang,
        review_id="REV-2026-009",
        milestone_id="MS-1",
        scope="acceptance",
        verdict="通过验收：仿真指标达标，文档齐备",
        occurred_at=dt("2026-08-22T09:00:00"),
    )
    svc.request_acceptance(
        zhang,
        milestone_id="MS-1",
        claimed_metrics={"仿真疲劳寿命_万次": 12180},
        occurred_at=dt("2026-08-23T09:00:00"),
    )
    svc.approve_acceptance(
        zhao,
        milestone_id="MS-1",
        final_metrics={"仿真疲劳寿命_万次": 12180},
        review_id="REV-2026-009",
        occurred_at=dt("2026-08-26T10:00:00"),
    )

    # 阶段二验收：按切换后的最终承诺版本指标判定，最终达标
    svc.request_acceptance(
        zhang,
        milestone_id="MS-2",
        claimed_metrics={"疲劳寿命_h": 12100, "传动误差_arcmin": 0.6, "传动效率": 0.91},
        occurred_at=dt("2026-09-02T09:00:00"),
    )
    svc.record_external_review(
        wang,
        review_id="REV-2026-012",
        milestone_id="MS-2",
        scope="acceptance",
        verdict="通过验收：新路线样机三项指标均达标，失败价值利用充分",
        recommendation="建议将复合材料柔轮失败知识库纳入平台长期保留",
        data_ref="s3://review/2026/012-acceptance.pdf",
        occurred_at=dt("2026-09-07T13:00:00"),
    )
    svc.approve_acceptance(
        zhao,
        milestone_id="MS-2",
        final_metrics={"疲劳寿命_h": 12100, "传动误差_arcmin": 0.6, "传动效率": 0.91},
        review_id="REV-2026-012",
        occurred_at=dt("2026-09-10T10:00:00"),
    )

    # =====================================================================
    # 项目二：伺服驱动器——暂停后处置义务持续有效
    # =====================================================================
    svc.register_project(
        zhou,
        project_code="DRV-2026-011",
        title="高动态伺服驱动器研发",
        research_goal="研制带宽2kHz以上的机器人关节伺服驱动器",
        applicant_name="云驱电控科技有限公司",
        total_budget=1_200_000,
        occurred_at=dt("2026-02-09T09:00:00"),
        ip_ownership="承担方与资助方共有（承担方70%，资助方30%）",
    )
    svc.establish_budget(
        zhou,
        project_code="DRV-2026-011",
        items=[
            {"item_id": "B-DRV-EQ", "name": "驱动测试平台", "category": "设备费", "amount": 600_000},
            {"item_id": "B-DRV-MAT", "name": "样机物料", "category": "材料费", "amount": 300_000},
            {"item_id": "B-DRV-TEST", "name": "测试费", "category": "测试费", "amount": 300_000},
        ],
        basis="立项预算（版本1）",
        occurred_at=dt("2026-02-09T09:20:00"),
    )
    svc.propose_milestone(
        zhou,
        milestone_id="MS-D1",
        project_code="DRV-2026-011",
        stage_index=1,
        name="驱动器原理样机",
        objective="完成原理样机设计与初步带载测试",
        acceptance_metrics=[{"name": "电流环带宽_kHz", "operator": ">=", "target": 2.0}],
        evidence_requirements=[
            {"code": "R1", "name": "带载测试报告", "kind": "test_result", "required": True},
        ],
        linked_budget_items=["B-DRV-EQ", "B-DRV-MAT", "B-DRV-TEST"],
        amount=900_000,
        due_date="2026-09-30",
        occurred_at=dt("2026-02-12T10:00:00"),
    )
    svc.commit_milestone(zhao, milestone_id="MS-D1", occurred_at=dt("2026-02-16T11:00:00"))
    svc.submit_evidence(
        zhou,
        evidence_id="EV-DRV-T1",
        milestone_id="MS-D1",
        requirement_code="R1",
        kind="test_result",
        title="原理样机初步带载测试",
        result="电流环带宽 1.4kHz，未达标，迭代中",
        data_ref="s3://rd/drv011/test-phase1.pdf",
        occurred_at=dt("2026-06-18T15:00:00"),
    )
    svc.verify_evidence(
        li, evidence_id="EV-DRV-T1", accepted=False,
        review_note="数据真实但指标未达承诺，暂不具备拨款条件",
        occurred_at=dt("2026-06-24T10:00:00"),
    )

    # 暂停：同步登记设备与未用资金处置义务
    svc.suspend(
        zhao,
        scope="DRV-2026-011",
        reason="承担方核心团队变动，申请暂停6个月；非因试验失败否定项目",
        disposal_obligations=[
            {
                "asset_or_fund": "高温老化测试箱（资产号 EQ-0917）",
                "type": "equipment",
                "action": "保管封存",
                "deadline": "2026-10-31",
                "responsible_id": "A-zhou",
            },
            {
                "asset_or_fund": "未用拨付资金 460000 元",
                "type": "unused_funds",
                "action": "资金退回",
                "deadline": "2026-09-30",
                "responsible_id": "FN-sun",
            },
        ],
        occurred_at=dt("2026-07-01T09:00:00"),
    )
    # 设备封存义务已履行并留证；未用资金退回义务仍 open——暂停期间义务不免除
    svc.fulfill_obligation(
        zhou,
        obligation_ref="DRV-2026-011-OB1",
        action_taken="测试箱断电封存于B3库房，贴封条并完成季度盘点",
        proof_ref="s3://rd/drv011/seal-record-0917.pdf",
        occurred_at=dt("2026-07-08T14:00:00"),
    )

    return store


def main() -> None:
    store = build()
    out = Path(__file__).resolve().parents[1] / "fixtures" / "events.json"
    out.write_text(store.export_json(), encoding="utf-8")

    events = store.all_events()
    print(f"已生成 {len(events)} 个事件 -> {out}")

    candidates = projections.acceptance_candidates(store)
    print(f"\n验收选取（中途切换路线且最终达标）：{len(candidates)} 个")
    for c in candidates:
        print(f"  - {c['project_code']} / {c['milestone_id']} {c['name']}")
        print(f"    路线：{c['route_before']} -> {c['route_after']}")
        print(f"    里程碑金额重估：{c['milestone_amount_before_switch']:,} -> {c['milestone_amount_after_switch']:,}")
        print(f"    保留的失败证据：{[f['evidence_id'] for f in c['preserved_failures']]}")
        print(f"    付款与快照：{[(p['payment_id'], p['commit_revision'], p['snapshot_id']) for p in c['payments']]}")
        print(f"    最终指标：{c['final_metrics']}")

    print("\n资金-证据追溯：")
    for row in projections.payment_trace(store):
        print(f"  - {row['payment_id']} {row['amount']:,} 元 @ 承诺版本{row['commit_revision']} 快照{row['snapshot_id']}")
        for e in row["evidence_entries"]:
            tag = f"（事后由 {e['superseded_later_by']} 更正，付款仍指向批准时版本）" if e["superseded_later_by"] else ""
            print(f"      {e['requirement_code']}: {e['evidence_id']} v{e['version']} {tag}")

    print("\n暂停项目处置义务：")
    for ob in projections.disposal_obligations(store):
        print(f"  - [{ob['status']}] {ob['obligation_ref']} {ob['asset_or_fund']} -> {ob['action']}")


if __name__ == "__main__":
    main()
