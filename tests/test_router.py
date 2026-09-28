import json
import shutil
import subprocess

import pytest

from tasky import router
from tasky.supervise import supervise

ALL = dict.fromkeys(router.MODELS, True)


def _proc(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def _runner(decision, code=0, stderr=""):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _proc(json.dumps(decision) if decision is not None else "", code, stderr)

    run.calls = calls
    return run


def _chain(*labels_reasons, outcome):
    """A nested mm-classifier decision, outermost first."""
    node = None
    for label, reason in reversed(labels_reasons):
        node = {
            "label": label,
            "reason": reason,
            "outcome": outcome,
            **({"next": node} if node else {}),
        }
    return node


@pytest.fixture
def classifier(tmp_path):
    path = tmp_path / "mm-classifier.mjs"
    path.write_text("// stub", encoding="utf-8")
    return {"TASKY_MM_CLASSIFIER": str(path)}


# --- routing table -----------------------------------------------------------------------------


def test_flags_omit_high_effort_and_haiku_effort():
    assert router.flags(router.Choice("opus", "xhigh")) == ["--model", "opus", "--effort", "xhigh"]
    assert router.flags(router.Choice("sonnet", "high")) == ["--model", "sonnet"]
    assert router.flags(router.Choice("haiku")) == ["--model", "haiku"]


def test_choose_falls_back_to_second_then_none():
    assert router.choose("plan", ALL)[0] == router.Choice("opus", "xhigh")
    assert router.choose("plan", {**ALL, "opus": False})[0] == router.Choice("sonnet", "xhigh")
    choice, why = router.choose("frontier", {**ALL, "fable": False, "opus": False})
    assert choice is None and "switched off" in why
    assert router.choose("default", ALL) == (None, "no route for this kind")
    assert router.choose("unknown-kind", ALL)[0] is None


def test_every_route_uses_known_models_and_efforts():
    for first, second in router.ROUTES.values():
        for choice in (first, second):
            if choice is not None:
                assert choice.model in router.MODELS
                assert choice.effort is None or choice.effort in router.EFFORTS
                assert choice.model != "haiku" or choice.effort is None


# --- classification ----------------------------------------------------------------------------


def test_classify_reads_the_chain_and_how_it_was_decided(tmp_path):
    run = _runner(_chain(("debug", "rule"), ("unclear", "rule"), outcome="debug-unclear"))
    assert router.classify({"title": "t", "body": "b"}, tmp_path / "c.mjs", run) == (
        "debug-unclear",
        "rules",
    )
    argv, kwargs = run.calls[0]
    assert argv[0] == "node" and argv[2] == "run" and argv[3].endswith("task-kind.json")
    assert json.loads(kwargs["input"]) == {"title": "t", "body": "b"}
    assert kwargs["timeout"] == router.CLASSIFIER_TIMEOUT_S

    run = _runner(_chain(("build", "rule"), ("ui", "routed"), outcome="ui"))
    assert router.classify({}, tmp_path / "c.mjs", run) == ("ui", "model")


def test_frontier_needs_a_rule(tmp_path):
    by_rule = _runner(_chain(("frontier", "rule"), outcome="frontier"))
    assert router.classify({}, tmp_path / "c", by_rule)[0] == "frontier"
    by_model = _runner(_chain(("frontier", "routed"), outcome="frontier"))
    assert router.classify({}, tmp_path / "c", by_model)[0] == "default"


@pytest.mark.parametrize(
    "run",
    [
        _runner(None, code=3, stderr="no Laya server at http://127.0.0.1:8177"),
        _runner(None),
        _runner({"label": "x"}),
    ],
)
def test_classify_raises_when_no_kind_can_be_read(tmp_path, run):
    with pytest.raises(RuntimeError):
        router.classify({}, tmp_path / "c", run)


def test_classify_raises_on_timeout(tmp_path):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(RuntimeError, match="did not run"):
        router.classify({}, tmp_path / "c", slow)


# --- decide ------------------------------------------------------------------------------------


def test_decide_off_does_nothing(classifier):
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    assert router.decide({}, False, ALL, env=classifier, runner=run) == router.Decision()
    assert run.calls == []


def test_decide_routes_and_explains(classifier):
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    decision = router.decide(
        {"body": "diseña la arquitectura"}, True, ALL, env=classifier, runner=run
    )
    assert decision.args == ["--model", "opus", "--effort", "xhigh"]
    assert (decision.model, decision.effort) == ("opus", "xhigh")
    assert decision.reason == "plan (rules) -> opus xhigh"


def test_decide_records_haiku_without_effort(classifier):
    run = _runner(_chain(("explore", "rule"), outcome="explore"))
    decision = router.decide({}, True, ALL, env=classifier, runner=run)
    assert decision.args == ["--model", "haiku"] and decision.effort is None


def test_decide_without_classifier_adds_nothing(tmp_path):
    env = {"TASKY_MM_CLASSIFIER": str(tmp_path / "missing.mjs")}
    decision = router.decide({}, True, ALL, env=env)
    assert decision.args == [] and "not found" in decision.reason


def test_decide_with_failing_classifier_adds_nothing(classifier):
    decision = router.decide({}, True, ALL, env=classifier, runner=_runner(None, code=1))
    assert decision.args == [] and decision.reason.startswith("no route: classifier failed")


def test_decide_with_all_models_off_adds_nothing(classifier):
    run = _runner(_chain(("docs", "rule"), outcome="docs"))
    decision = router.decide(
        {}, True, {**ALL, "sonnet": False, "haiku": False}, env=classifier, runner=run
    )
    assert decision.args == [] and decision.kind == "docs" and "switched off" in decision.reason


# --- classifier discovery and settings ---------------------------------------------------------


def test_find_classifier_prefers_env_then_newest_plugin_version(tmp_path):
    cache = tmp_path / "plugins" / "cache" / "rixmerz" / "muscle-memory"
    for version in ("0.9.0", "0.10.0", "0.2.1"):
        bin_dir = cache / version / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mm-classifier.mjs").write_text("", encoding="utf-8")
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}
    assert router.find_classifier(env).parent.parent.name == "0.10.0"
    explicit = tmp_path / "x.mjs"
    explicit.write_text("", encoding="utf-8")
    assert router.find_classifier({**env, "TASKY_MM_CLASSIFIER": str(explicit)}) == explicit
    assert router.find_classifier({"CLAUDE_CONFIG_DIR": str(tmp_path / "none")}) is None


def test_settings_default_off_with_every_model_allowed(store):
    assert router.load_settings(store) == {"enabled": False, "models": ALL}
    saved = router.save_settings(store, enabled=True, models={"fable": False})
    assert saved == {"enabled": True, "models": {**ALL, "fable": False}}
    assert router.load_settings(store) == saved


def test_laya_up_only_probes_loopback():
    assert router.laya_up({"MM_LAYA_URL": "http://example.com"}) is False
    assert router.laya_up({"MM_LAYA_URL": "http://127.0.0.1:9"}) is False


# --- supervise integration ---------------------------------------------------------------------


def _running(store, tmp_path, body):
    task = store.create_task(
        kind="prompt", body=body, status="queued", source="ui", cwd=str(tmp_path)
    )
    store.upsert_session("sess-1", cwd=str(tmp_path), source="worker")
    store.claim_task(task["id"], status="running", session_id="sess-1")
    return store.get_task(task["id"])


def _supervise(config, task, monkeypatch, decision=None, code=0):
    launched = []

    class Proc:
        def wait(self):
            return 0

    def popen(argv, **kwargs):
        launched.append(argv)
        return Proc()

    if decision is not None:
        run = _runner(decision, code=code)
        real_decide = router.decide
        monkeypatch.setattr(
            router, "decide", lambda t, e, a, **kw: real_decide(t, e, a, env={}, runner=run)
        )
        monkeypatch.setattr(router, "find_classifier", lambda env=None: config.home / "c.mjs")
    config.log_dir.mkdir(parents=True, exist_ok=True)
    prompt = config.log_dir / f"task-{task['id']}.prompt"
    prompt.write_text(task["body"], encoding="utf-8")
    supervise(
        config,
        task["id"],
        "sess-1",
        str(prompt),
        str(config.log_dir / "t.log"),
        ["claude", "-p", "--session-id", "sess-1"],
        popen=popen,
    )
    return launched[0]


def test_supervise_leaves_argv_untouched_when_router_is_off(store, config, tmp_path, monkeypatch):
    task = _running(store, tmp_path, "diseña la arquitectura")
    argv = _supervise(config, task, monkeypatch, _chain(("plan", "rule"), outcome="plan"))
    assert argv == ["claude", "-p", "--session-id", "sess-1"]
    assert store.get_task(task["id"])["route_reason"] is None


def test_supervise_appends_route_and_records_it(store, config, tmp_path, monkeypatch):
    router.save_settings(store, enabled=True)
    task = _running(store, tmp_path, "diseña la arquitectura")
    argv = _supervise(config, task, monkeypatch, _chain(("plan", "rule"), outcome="plan"))
    assert argv == [
        "claude",
        "-p",
        "--session-id",
        "sess-1",
        "--model",
        "opus",
        "--effort",
        "xhigh",
    ]
    recorded = store.get_task(task["id"])
    assert (recorded["model"], recorded["effort"]) == ("opus", "xhigh")
    assert recorded["route_reason"] == "plan (rules) -> opus xhigh"


def test_supervise_respects_a_switched_off_model(store, config, tmp_path, monkeypatch):
    router.save_settings(store, enabled=True, models={"fable": False, "opus": False})
    task = _running(store, tmp_path, "#fable prueba de frontera")
    argv = _supervise(config, task, monkeypatch, _chain(("frontier", "rule"), outcome="frontier"))
    assert argv == ["claude", "-p", "--session-id", "sess-1"]
    assert "switched off" in store.get_task(task["id"])["route_reason"]


def test_supervise_survives_a_failing_classifier(store, config, tmp_path, monkeypatch):
    router.save_settings(store, enabled=True)
    task = _running(store, tmp_path, "algo")
    argv = _supervise(config, task, monkeypatch, _chain(("x", "rule"), outcome="x"), code=3)
    assert argv == ["claude", "-p", "--session-id", "sess-1"]
    assert store.get_task(task["id"])["route_reason"].startswith("no route: classifier failed")


# --- pins: the user's own model for one task ---------------------------------------------------


@pytest.mark.parametrize(
    ("pin", "args", "applied", "reason"),
    [
        (("opus", "xhigh"), ["--model", "opus", "--effort", "xhigh"], ("opus", "xhigh"),
         "opus xhigh, your choice"),
        (("sonnet", "high"), ["--model", "sonnet"], ("sonnet", "high"), "sonnet high, your choice"),
        (("sonnet", None), ["--model", "sonnet"], ("sonnet", "high"), "sonnet high, your choice"),
        (("haiku", "max"), ["--model", "haiku"], ("haiku", None), "haiku, your choice"),
        (("default", None), [], (None, None), "session default, your choice"),
    ],
)
def test_a_pin_replaces_classification_router_on_or_off(classifier, pin, args, applied, reason):
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    task = {"body": "diseña la arquitectura", "pinned_model": pin[0], "pinned_effort": pin[1]}
    for enabled in (True, False):
        decision = router.decide(task, enabled, ALL, env=classifier, runner=run)
        assert decision.args == args
        assert (decision.model, decision.effort) == applied
        assert decision.reason == reason and decision.pinned is True
    assert run.calls == []


def test_a_pin_ignores_the_model_switches(classifier):
    off = dict.fromkeys(router.MODELS, False)
    task = {"pinned_model": "fable", "pinned_effort": "high"}
    assert router.decide(task, True, off, env=classifier).args == ["--model", "fable"]


def test_a_pin_to_an_unknown_model_is_ignored(classifier):
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    task = {"body": "x", "pinned_model": "gpt", "pinned_effort": "high"}
    assert router.decide(task, False, ALL, env=classifier, runner=run) == router.Decision()
    routed = router.decide(task, True, ALL, env=classifier, runner=run)
    assert routed.args == ["--model", "opus", "--effort", "xhigh"] and not routed.pinned


# --- previews ----------------------------------------------------------------------------------

ON = {"enabled": True, "models": ALL}


def _queued(store, body, **fields):
    task = store.create_task(kind="prompt", body=body, status="queued", source="ui")
    return store.update_task(task["id"], **fields) if fields else task


def test_refresh_previews_classifies_each_waiting_task_once(store, classifier):
    plan = _queued(store, "diseña la arquitectura")
    _queued(store, "pinned", pinned_model="opus")
    done = store.create_task(kind="prompt", body="old", status="done", source="ui")
    run = _runner(_chain(("plan", "rule"), outcome="plan"))

    assert router.refresh_previews(store, ON, env=classifier, runner=run) == 1
    assert len(run.calls) == 1  # neither the pinned nor the finished task was classified
    cached = store.get_task(plan["id"])
    assert (cached["preview_kind"], cached["preview_note"]) == ("plan", "rules")
    assert store.get_task(done["id"])["preview_key"] is None

    assert router.refresh_previews(store, ON, env=classifier, runner=run) == 0
    store.update_task(plan["id"], body="otra cosa")
    assert router.refresh_previews(store, ON, env=classifier, runner=run) == 1
    assert len(run.calls) == 2


def test_refresh_previews_does_nothing_with_the_router_off(store, classifier):
    _queued(store, "diseña la arquitectura")
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    off = {"enabled": False, "models": ALL}
    assert router.refresh_previews(store, off, env=classifier, runner=run) == 0
    assert run.calls == []


def test_refresh_previews_caches_a_failure_until_the_classifier_changes(store, tmp_path):
    task = _queued(store, "algo")
    missing = {"TASKY_MM_CLASSIFIER": str(tmp_path / "missing.mjs")}
    assert router.refresh_previews(store, ON, env=missing) == 1
    assert router.refresh_previews(store, ON, env=missing) == 0
    cached = store.get_task(task["id"])
    assert cached["preview_kind"] is None and "not found" in cached["preview_note"]
    assert router.preview(cached, ON, None)["model"] is None

    installed = tmp_path / "mm-classifier.mjs"
    installed.write_text("", encoding="utf-8")
    run = _runner(_chain(("docs", "rule"), outcome="docs"))
    env = {"TASKY_MM_CLASSIFIER": str(installed)}
    assert router.refresh_previews(store, ON, env=env, runner=run) == 1
    assert store.get_task(task["id"])["preview_kind"] == "docs"


def test_preview_follows_the_switches_without_classifying_again(store, classifier):
    path = router.find_classifier(classifier)
    task = _queued(store, "diseña la arquitectura")
    run = _runner(_chain(("plan", "rule"), outcome="plan"))
    router.refresh_previews(store, ON, env=classifier, runner=run)
    task = store.get_task(task["id"])
    assert router.preview(task, ON, path) == {
        "source": "router", "model": "opus", "effort": "xhigh", "reason": "plan (rules)",
    }
    no_opus = {"enabled": True, "models": {**ALL, "opus": False}}
    assert router.preview(task, no_opus, path)["model"] == "sonnet"
    neither = {"enabled": True, "models": {**ALL, "opus": False, "sonnet": False}}
    shown = router.preview(task, neither, path)
    assert shown["model"] is None and "switched off" in shown["reason"]
    assert router.preview(task, {"enabled": False, "models": ALL}, path) is None
    # Edited text: the cached kind no longer applies until the next pass.
    assert router.preview({**task, "body": "otra"}, ON, path)["source"] == "pending"
    assert len(run.calls) == 1


def test_preview_shows_a_pin_even_with_the_router_off():
    off = {"enabled": False, "models": ALL}
    shown = router.preview({"pinned_model": "haiku", "pinned_effort": None}, off, None)
    assert shown == {"source": "pinned", "model": "haiku", "effort": None, "reason": "your choice"}
    session = router.preview({"pinned_model": "default"}, off, None)
    assert session["source"] == "pinned" and session["model"] is None


def test_supervise_honours_a_pin_without_classifying(store, config, tmp_path, monkeypatch):
    router.save_settings(store, enabled=True)
    task = _running(store, tmp_path, "diseña la arquitectura")
    store.update_task(task["id"], pinned_model="sonnet", pinned_effort="low")
    classified = []

    def classify(*args, **kwargs):
        classified.append(args)
        return "plan", "rules"

    monkeypatch.setattr(router, "classify", classify)
    argv = _supervise(config, store.get_task(task["id"]), monkeypatch)
    assert argv == [
        "claude", "-p", "--session-id", "sess-1", "--model", "sonnet", "--effort", "low",
    ]
    assert classified == []
    recorded = store.get_task(task["id"])
    assert (recorded["model"], recorded["effort"]) == ("sonnet", "low")
    assert recorded["route_reason"] == "sonnet low, your choice"


def test_supervise_honours_a_pin_with_the_router_off(store, config, tmp_path, monkeypatch):
    task = _running(store, tmp_path, "hola")
    store.update_task(task["id"], pinned_model="opus", pinned_effort="max")
    argv = _supervise(config, store.get_task(task["id"]), monkeypatch)
    assert argv == ["claude", "-p", "--session-id", "sess-1", "--model", "opus", "--effort", "max"]
    assert store.get_task(task["id"])["route_reason"] == "opus max, your choice"


def test_supervise_session_default_pin_keeps_argv_untouched(store, config, tmp_path, monkeypatch):
    router.save_settings(store, enabled=True)
    task = _running(store, tmp_path, "diseña la arquitectura")
    store.update_task(task["id"], pinned_model="default")
    plan = _chain(("plan", "rule"), outcome="plan")
    argv = _supervise(config, store.get_task(task["id"]), monkeypatch, plan)
    assert argv == ["claude", "-p", "--session-id", "sess-1"]
    recorded = store.get_task(task["id"])
    assert recorded["model"] is None
    assert recorded["route_reason"] == "session default, your choice"


# --- the committed definitions, run through the real classifier when it is installed ------------

_MMC = router.find_classifier()


@pytest.mark.skipif(
    _MMC is None or shutil.which("node") is None, reason="mm-classifier not installed"
)
@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("dale commit y push recuerda subir la version changelog etc", "docs"),
        ("diseña la arquitectura del nuevo módulo de pagos", "plan"),
        ("el test falla a veces, intermitente, no sé por qué", "test"),
        ("tenemos que mejorar la UI para aprovechar mejor los margenes", "ui"),
        ("se cae con este traceback: KeyError en línea 40", "debug-repro"),
        ("revisa el código del login antes de mergear", "review-critical"),
        ("¿qué recomiendas hacer?", "chat"),
        ("#fable resuelve este problema de frontera", "frontier"),
        ("matalo inacap docente", "default"),
        ("realiza un analisis complejo de proyecto y comentame que tal", "plan"),
        ("realiza un analisis tu de la UI esta horriblemente fea", "ui"),
        ("que tipo de caso solicita analizar o que caracteristicas debe tener?", "chat"),
        ("realiza un vistazo rapido al readme y comentame de que se trata", "explore"),
        ("hola de que trata el proyecto", "explore"),
    ],
)
def test_committed_definitions_route_real_prompts(body, kind):
    assert router.classify({"title": body[:40], "body": body}, _MMC)[0] == kind
