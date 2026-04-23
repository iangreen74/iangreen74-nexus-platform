"""Mechanism 2 — deploy event writeback.

Subscribes to forgewing-deploy-events bus. For each event, Haiku
classifies what ontology object was implicated and enqueues proposals
into the shared classifier_proposals table.

Public API:
    classify_deploy_event(event_detail) -> list[DeployProposal]
    enqueue_proposals(proposals, db_conn) -> int
"""
