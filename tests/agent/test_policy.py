from src.agent.policy import HookPolicy, NeedsApproval, Proceed


def test_load_reads_gated_ops(tmp_path):
    (tmp_path / "before_remove.yaml").write_text(
        "op: remove\nrequires_approval: true\nquestion: Permanently delete?\n",
        encoding="utf-8",
    )
    policy = HookPolicy.load(tmp_path)
    assert "remove" in policy.gated_ops


def test_check_remove_unapproved_needs_approval():
    policy = HookPolicy(gated_ops={"remove": "Delete?"})
    dec = policy.check("remove", {"doc_id": "x"})
    assert isinstance(dec, NeedsApproval)
    assert dec.pending_action.op == "remove"
    assert dec.pending_action.params == {"doc_id": "x"}
    assert dec.question == "Delete?"


def test_check_remove_approved_proceeds():
    policy = HookPolicy(gated_ops={"remove": "Delete?"})
    assert isinstance(policy.check("remove", {"doc_id": "x"}, approved=True), Proceed)


def test_check_non_gated_op_proceeds():
    policy = HookPolicy(gated_ops={"remove": "Delete?"})
    assert isinstance(policy.check("recall", {"query": "q"}), Proceed)
