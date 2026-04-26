# Silent-Except Sweep — 2026-04-26

Raw evidence file. **No KEEP/REMOVE/INVESTIGATE classification yet** — that
is deferred to a Day-3 triage track, per the Source-Kind Step-0 decision
("Defer classification, commit raw evidence").

This file lists every site in production code (i.e., excluding `tests/`
and `test_*.py`) that swallows an exception and immediately returns a
sentinel (`[]` / `None` / `{}` / `set()` / `0`) or `pass`es, without an
intervening `log.*` / `logger.*` / `logging.*` call. These are the
candidates most likely to be masking real failures.

Captured by a deterministic regex pass over both repos at the time of
the migration-012 source_kind feature train. Generation rules:

  - Match `except Exception:` or `except Exception as <name>:`
  - Inspect the next two non-blank lines
  - If they contain `log.*` / `logger.*` / `logging.*`, **exclude** —
    the failure is at least observable
  - Otherwise, if the first non-blank line is a silent return or `pass`,
    **include** as a candidate

This is intentionally conservative: it under-counts (any except that
re-raises after logging is excluded) and over-counts (legitimate cache-
miss / optional-data fallbacks that genuinely should not log). Triage
each entry to KEEP / REMOVE / INVESTIGATE in a separate pass.

Two sites (`nexus/mechanism3/rules.py:125,151`) were fixed by the
companion PR-1 — they were masking missing-column schema drift. The
full count below reflects the BEFORE state for those two sites; after
PR-1 they become "let real errors propagate" sites.

---

## Counts

| Repo | Sites |
|---|---|
| `nexus-platform` | 81 |
| `aria-platform` | 199 |
| **Total** | **280** |

This is roughly **9× the prompt's expected ~30**. The original estimate
was a manual partial scan during checkpoint surfacing; the precise
regex pass found the rest. Surfacing the larger count is the substrate
truth this doc is supposed to encode.

---

## nexus-platform (81 sites)

```
nexus/aria/ontology_reader.py:168: return []
nexus/askcustomer/service.py:40: return None
nexus/auto_remediation.py:146: pass
nexus/capabilities/ci_heartbeat.py:70: return None
nexus/capabilities/ci_ops.py:79: pass
nexus/capabilities/ci_patterns.py:83: return {}
nexus/capabilities/consistency_auditor.py:53: pass
nexus/capabilities/consistency_auditor.py:154: return []
nexus/capabilities/deploy_cycle.py:189: pass
nexus/capabilities/deploy_cycle.py:262: pass
nexus/capabilities/goal_checks.py:155: return []
nexus/capabilities/investigation.py:165: return []
nexus/capabilities/onboarding_monitor.py:79: return None
nexus/capabilities/sprint_context.py:55: return None
nexus/capabilities/sprint_context.py:64: return None
nexus/capabilities/sprint_context.py:83: return None
nexus/capabilities/tenant_actions.py:160: pass
nexus/capabilities/timeline_resolution.py:43: return []
nexus/dashboard/ops_chat.py:82: pass
nexus/dashboard/routes.py:362: pass
nexus/dashboard/routes.py:1097: pass
nexus/dashboard/routes.py:1197: pass
nexus/dashboard/routes.py:1325: pass
nexus/dashboard/routes.py:1341: pass
nexus/dashboard/routes.py:1359: pass
nexus/dashboard/routes.py:1372: pass
nexus/dashboard/routes.py:1386: pass
nexus/dashboard/routes.py:1399: pass
nexus/dashboard/routes.py:1408: pass
nexus/dashboard/routes.py:1450: pass
nexus/dashboard/routes.py:1466: pass
nexus/dashboard/routes.py:1476: pass
nexus/dashboard/routes.py:1493: pass
nexus/deploy_decision.py:131: return 0
nexus/deploy_decision.py:140: return 0
nexus/deploy_decision.py:155: return 0
nexus/deploy_decision.py:164: return 0
nexus/engineering_patterns.py:99: return None
nexus/intelligence/dogfood_watch.py:68: pass
nexus/intelligence/learning_snapshot.py:79: return []
nexus/intelligence/report_queries.py:42: return []
nexus/intelligence/report_queries.py:59: return []
nexus/intelligence/report_queries.py:73: return set()
nexus/intelligence/report_queries.py:87: return set()
nexus/intelligence/report_queries.py:115: return []
nexus/learning_overview.py:228: return None
nexus/mechanism1/classifier.py:56: return None
nexus/mechanism3/store.py:35: pass
nexus/mechanism3/store.py:39: pass
nexus/ontology/eval_corpus.py:29: return None
nexus/overwatch_v2/tools/read_tools/read_aria_conversations.py:62: return []
nexus/overwatch_v2/tools/read_tools/read_customer_pipeline.py:49: return []
nexus/overwatch_v2/tools/read_tools/read_customer_pipeline.py:56: return []
nexus/overwatch_v2/tools/read_tools/read_customer_pipeline.py:79: return []
nexus/overwatch_v2/tools/read_tools/read_customer_tenant_state.py:85: return []
nexus/proactive_scanner.py:78: pass
nexus/proactive_scanner.py:112: pass
nexus/reasoning/executor.py:51: pass
nexus/reasoning/executor.py:328: pass
nexus/reasoning/executor.py:425: pass
nexus/reasoning/executor.py:477: pass
nexus/reasoning/executor.py:490: pass
nexus/reasoning/executor.py:497: pass
nexus/reasoning/executor.py:553: pass
nexus/reasoning/executor.py:615: pass
nexus/reasoning/heal_chain.py:111: pass
nexus/reasoning/pattern_learner.py:277: return []
nexus/reasoning/pattern_learner.py:284: pass
nexus/reasoning/triage.py:975: pass
nexus/reports/builders/pipeline_activity.py:46: return None
nexus/sensors/capability_discovery.py:121: pass
nexus/sensors/dogfood_reconciler.py:133: return []
nexus/sensors/sre_metrics.py:248: pass
nexus/sensors/tenant_health.py:274: pass
nexus/sensors/tenant_health.py:288: pass
nexus/summaries/generator.py:55: return None
nexus/synthetic_tests.py:190: pass
nexus/synthetic_tests.py:1031: pass
nexus/synthetic_tests.py:1041: pass
nexus/synthetic_tests.py:1046: return None
nexus/tenant_deep_dive.py:169: return None
```

## aria-platform (199 sites)

```
aria/agency/code_generator.py:36: pass
aria/agency/dev_command_processor.py:77: pass
aria/agency/environment_manager.py:32: pass
aria/agency/execution_pipeline.py:160: pass
aria/agency/healing_workflow.py:123: return []
aria/agency/proposal_executor.py:51: pass
aria/agency/proposal_manager.py:139: pass
aria/agency/task_decomposer.py:36: pass
aria/auto_remediation.py:140: pass
aria/bootstrap_self_monitor.py:64: pass
aria/builder/build_executor.py:114: pass
aria/builder/build_executor.py:130: pass
aria/builder/build_finalizer.py:85: pass
aria/builder/build_finalizer.py:190: pass
aria/builder/image_builder.py:96: pass
aria/builder/infra_provisioner.py:179: pass
aria/builder/infra_provisioner.py:197: pass
aria/builder/lambda_build.py:33: pass
aria/builder/orchestrator.py:174: pass
aria/builder/orchestrator.py:183: pass
aria/builder/preview_manager.py:147: pass
aria/builder/product_deployer.py:83: pass
aria/builder/product_deployer.py:157: pass
aria/builder/product_destroyer.py:124: pass
aria/builder/product_destroyer.py:133: pass
aria/cicd/deployment_assessor.py:59: return []
aria/cloudwatch_collector.py:49: pass
aria/customer/email_delivery.py:25: return None
aria/daemon.py:51: return {}
aria/ec2_lifecycle.py:49: pass
aria/ec2_lifecycle.py:164: pass
aria/intelligence/archive/plan_reasoner.py:136: pass
aria/intelligence/archive/proactive_engine.py:117: pass
aria/intelligence/archive/product_suggestions.py:148: return []
aria/intelligence/archive/temporal_correlation.py:106: pass
aria/intelligence/ci_context.py:80: pass
aria/intelligence/ci_sherpa.py:137: return None
aria/intelligence/consolidation_engine.py:139: pass
aria/intelligence/cyclical_patterns.py:34: pass
aria/intelligence/deep_context.py:59: pass
aria/intelligence/deep_context.py:84: pass
aria/intelligence/deep_context.py:96: pass
aria/intelligence/deep_context.py:111: pass
aria/intelligence/deep_context.py:127: pass
aria/intelligence/deep_context.py:137: pass
aria/intelligence/deep_context.py:152: pass
aria/intelligence/deliberation_engine.py:87: pass
aria/intelligence/journey_graph.py:53: return []
aria/intelligence/journey_graph.py:67: return []
aria/intelligence/journey_graph.py:80: return {}
aria/intelligence/research_agent.py:97: return []
aria/intelligence/research_agent.py:117: return []
aria/intelligence/research_agent.py:130: return []
aria/intelligence/research_agent.py:149: return []
aria/intelligence/simulation_engine.py:176: return {}
aria/intelligence/slack_brain.py:56: pass
aria/intelligence/slack_brain.py:85: pass
aria/intelligence/slack_notifier.py:82: pass
aria/intelligence/state_history.py:52: pass
aria/intelligence/state_tensor.py:59: pass
aria/intelligence/telegram_bot.py:60: pass
aria/intelligence/user_learning.py:117: pass
aria/intelligence/verification_orchestrator.py:95: pass
aria/intelligence/verification_orchestrator.py:149: pass
aria/knowledge/live_status.py:113: return None
aria/knowledge/live_status.py:142: return None
aria/memory.py:129: pass
aria/mission_manager.py:136: pass
aria/onboarding.py:116: pass
aria/onboarding.py:138: pass
aria/ontology_events.py:28: return None
aria/operator.py:134: pass
aria/operator.py:153: pass
aria/platform/aria_chat.py:182: pass
aria/platform/aria_chat.py:190: pass
aria/platform/aria_proactive.py:76: pass
aria/platform/aria_proactive.py:152: pass
aria/platform/aria_voice.py:155: pass
aria/platform/sre_history.py:54: pass
aria/platform/sre_history.py:71: pass
aria/platform/sre_history.py:95: pass
aria/platform/sre_metrics.py:18: return None
aria/platform/synthetic_tester.py:69: pass
aria/project/security_scanner.py:34: pass
aria/proposals/writer.py:166: pass
aria/remote_engineer/accretion_sources/completed_tasks.py:28: pass
aria/remote_engineer/accretion_sources/conventions.py:8: return None
aria/remote_engineer/accretion_sources/dependency_graph.py:25: pass
aria/remote_engineer/accretion_sources/mission_brief.py:17: pass
aria/remote_engineer/accretion_sources/mission_brief.py:26: pass
aria/remote_engineer/accretion_sources/strategic_context.py:19: return None
aria/remote_engineer/accretion_sources/user_portrait.py:16: pass
aria/remote_engineer/aws_scanners.py:138: pass
aria/remote_engineer/blueprint_approver.py:51: return None
aria/remote_engineer/brief_log.py:33: pass
aria/remote_engineer/deployment/bootstrap_updater.py:67: pass
aria/remote_engineer/deployment/bootstrap_updater.py:102: pass
aria/remote_engineer/deployment/deep_code_analyzer.py:108: return None
aria/remote_engineer/deployment/deep_code_analyzer.py:116: return None
aria/remote_engineer/deployment/deploy_attempt_recorder.py:135: return None
aria/remote_engineer/deployment/deploy_readiness.py:98: pass
aria/remote_engineer/deployment/deploy_readiness.py:121: pass
aria/remote_engineer/deployment/deploy_signals.py:116: pass
aria/remote_engineer/deployment/deploy_signals.py:196: return {}
aria/remote_engineer/deployment/deployment_blueprint.py:75: return None
aria/remote_engineer/deployment/deployment_investigator.py:199: pass
aria/remote_engineer/deployment/ecs_runtime_monitor.py:71: return None
aria/remote_engineer/deployment/ecs_runtime_monitor.py:98: pass
aria/remote_engineer/deployment/infra_template_generator.py:42: pass
aria/remote_engineer/deployment/intelligent_executor.py:95: pass
aria/remote_engineer/deployment/multi_service_monitor.py:55: pass
aria/remote_engineer/deployment/multi_service_monitor.py:62: pass
aria/remote_engineer/deployment/multi_service_monitor.py:107: pass
aria/remote_engineer/deployment/multi_service_orchestrator.py:141: pass
aria/remote_engineer/deployment/multi_service_orchestrator.py:145: pass
aria/remote_engineer/deployment/predictive_deployer.py:93: pass
aria/remote_engineer/deployment/preview_deployer.py:110: pass
aria/remote_engineer/deployment/preview_infra.py:64: return 0
aria/remote_engineer/deployment/roadmap_integration.py:19: return []
aria/remote_engineer/deployment/universal_deploy_reasoner.py:158: pass
aria/remote_engineer/deployment/universal_deploy_reasoner.py:175: pass
aria/remote_engineer/deployment/workflow_healer.py:170: pass
aria/remote_engineer/github_user_auth.py:76: return None
aria/remote_engineer/github_user_auth.py:105: return {}
aria/remote_engineer/github_user_auth.py:114: return None
aria/remote_engineer/intelligence/context_injector.py:128: pass
aria/remote_engineer/monitoring/production_metrics.py:125: pass
aria/remote_engineer/monitoring/production_metrics.py:145: return None
aria/remote_engineer/product_synthesizer.py:74: return None
aria/remote_engineer/quality_gate.py:103: pass
aria/remote_engineer/quality_gate.py:116: pass
aria/remote_engineer/task_prompt_builder.py:31: pass
aria/remote_engineer/task_prompt_builder.py:37: pass
aria/remote_engineer/terraform_reader.py:139: return []
aria/remote_engineer/testing/error_monitor.py:149: return []
aria/remote_engineer/testing/smoke_tester.py:173: return []
aria/requirements_checker_helpers.py:14: pass
aria/unreal_project_analyzer.py:175: pass
aria/wake_protocol.py:62: pass
auth.py:71: return {}
cicd-upload/deploy_readiness.py:98: pass
cicd-upload/deploy_readiness.py:121: pass
cicd-upload/quality_gate.py:102: pass
cicd-upload/quality_gate.py:115: pass
cicd-upload/task_executor.py:180: return None
connectors/self/connector.py:56: pass
connectors/self/connector.py:70: pass
connectors/self/git_analyzer.py:119: return []
connectors/universal/discovery.py:34: pass
connectors/universal/observer.py:140: pass
console/aws_routes.py:19: pass
console/billing_routes.py:18: pass
console/build_routes.py:22: pass
console/chat_routes.py:39: pass
console/ci_status_routes.py:22: pass
console/ci_status_routes.py:41: pass
console/cto_execute_routes.py:154: pass
console/dashboard_helpers.py:24: return []
console/dashboard_helpers.py:54: return []
console/github_routes.py:17: pass
console/intelligence_observatory_routes.py:90: pass
console/orgagi_routes.py:15: pass
console/preview_routes.py:17: pass
console/preview_routes.py:66: pass
console/product_chat_routes.py:18: pass
console/product_sre_routes.py:17: pass
console/product_sre_routes.py:40: pass
console/registration_routes.py:73: pass
console/server.py:64: pass
console/slack_routes.py:56: pass
console/stripe_routes.py:17: pass
console/temporal_routes.py:89: pass
deploy/lambda/healing/decide.py:34: pass
deploy/lambda/healing/notify.py:32: pass
deploy/lambda/waitlist/handler.py:51: pass
forgescaler/activation.py:54: pass
forgescaler/activation.py:88: pass
forgescaler/activation.py:98: pass
forgescaler/activation.py:106: pass
forgescaler/activation.py:116: pass
forgescaler/admin_routes.py:73: pass
forgescaler/conversation_routes.py:96: pass
forgescaler/github_routes.py:141: pass
forgescaler/github_routes.py:185: pass
forgescaler/guidance_data.py:163: pass
forgescaler/guidance_data.py:179: pass
forgescaler/preview_routes.py:52: pass
forgescaler/project_routes.py:45: pass
forgescaler/voice_routes.py:47: pass
forgescaler/voice_routes.py:139: pass
forgescaler/voice_routes.py:155: pass
forgescaler/voice_routes.py:173: pass
infrastructure/watchdog-lambda/handler.py:75: return []
tests/archive_test_simulation_engine.py:15: pass
tests/integration/conftest.py:43: pass
tests/smoke_test.py:55: pass
tests/smoke_test.py:122: pass
tests/smoke_test.py:142: pass
upload-files/conversation.py:38: pass
```

---

## Notes

- `nexus/mechanism3/rules.py:125,151` are addressed in the companion
  PR-1. Removing the silent except let real DB errors propagate to
  `scan_tenant()`'s outer try/except handler. The missing-mechanism2-producer
  state is now observable via a `log.warning` at the call site.
- All other entries remain as-is in this PR. Day-3 triage decides each.
- Several of these are likely legitimate (optional-data fallbacks,
  cache-miss paths, defense in depth around best-effort observability
  writes). The triage step is to read each in context, not auto-fix.
