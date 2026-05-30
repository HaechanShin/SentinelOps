from slack_app.handlers.alert_handler import build_alert_blocks


def test_build_alert_blocks_sentiment_drop(sample_alert):
    blocks = build_alert_blocks(sample_alert)

    assert len(blocks) >= 3
    assert blocks[0]["type"] == "header"
    assert "Sentiment Drop" in blocks[0]["text"]["text"]

    section = blocks[2]
    assert section["type"] == "section"
    field_texts = [f["text"] for f in section["fields"]]
    assert any("HIGH" in t for t in field_texts)
    assert any("0.45" in t for t in field_texts)


def test_build_alert_blocks_keyword_spike():
    alert = {
        "id": "test-alert-002",
        "alert_type": "keyword_spike",
        "severity": "medium",
        "trigger_data": {
            "keyword": "cheat",
            "recent_count": 25,
            "earlier_count": 8,
            "multiplier": 3.12,
            "representative_posts": [],
        },
    }

    blocks = build_alert_blocks(alert)

    assert blocks[0]["type"] == "header"
    assert "Keyword Spike" in blocks[0]["text"]["text"]


def test_build_alert_blocks_with_drafts(sample_alert):
    drafts = [
        {
            "id": "draft-001",
            "content": "We are aware of the server issues.",
            "tone": "official",
        },
        {
            "id": "draft-002",
            "content": "We understand your frustration.",
            "tone": "empathetic",
        },
    ]

    blocks = build_alert_blocks(sample_alert, drafts)

    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 2

    buttons = action_blocks[0]["elements"]
    button_texts = [b["text"]["text"] for b in buttons]
    assert "✅ Approve" in button_texts
    assert "✏️ Edit" in button_texts
    assert "❌ Reject" in button_texts


def test_build_alert_blocks_without_drafts(sample_alert):
    blocks = build_alert_blocks(sample_alert, drafts=None)

    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) >= 1

    last_actions = action_blocks[-1]["elements"]
    action_ids = [b["action_id"] for b in last_actions]
    assert any("generate_draft" in a for a in action_ids)
    assert any("dismiss_alert" in a for a in action_ids)
