import os
from typing import Optional
import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import anthropic

app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude_client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    timeout=30.0,
)

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

# 重複処理防止
processed_events = set()


def get_block_text(block: dict) -> str:
    block_type = block.get("type", "")
    text_types = [
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item", "to_do",
        "quote", "callout"
    ]
    if block_type in text_types:
        rich_text = block.get(block_type, {}).get("rich_text", [])
        return "".join([t.get("plain_text", "") for t in rich_text])
    return ""


def get_page_content(page_id: str, depth: int = 0) -> str:
    if depth > 2:
        return ""
    response = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        params={"page_size": 100}
    )
    if response.status_code != 200:
        return ""
    blocks = response.json().get("results", [])
    texts = []
    for block in blocks:
        text = get_block_text(block)
        if text:
            texts.append(text)
        if block.get("has_children") and depth < 2:
            child_text = get_page_content(block["id"], depth + 1)
            if child_text:
                texts.append(child_text)
    return "\n".join(texts)


def search_notion(query: str) -> Optional[str]:
    response = requests.post(
        "https://api.notion.com/v1/search",
        headers=NOTION_HEADERS,
        json={
            "query": query,
            "page_size": 5,
            "filter": {"value": "page", "property": "object"}
        }
    )
    if response.status_code != 200:
        return None
    results = response.json().get("results", [])
    if not results:
        return None

    content_parts = []
    for page in results[:3]:
        page_id = page["id"]
        title = ""
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_items = prop.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_items])
                break
        page_content = get_page_content(page_id)
        if page_content:
            content_parts.append(f"【{title}】\n{page_content}")

    return "\n\n---\n\n".join(content_parts) if content_parts else None


def ask_claude(question: str, manual_content: str) -> str:
    message = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""あなたはライカ5階ワークスペースのマニュアル検索アシスタントです。
以下のマニュアル内容を参照して、スタッフからの質問に日本語で答えてください。

【マニュアル内容】
{manual_content}

【質問】
{question}

回答のルール：
- マニュアルに記載がある内容のみ回答する
- 記載がない場合は「マニュアルに記載がありません。管理者にご確認ください。」と答える
- 箇条書きを使って分かりやすく回答する
- 丁寧だが簡潔に回答する"""
        }]
    )
    return message.content[0].text


@app.event("app_mention")
def handle_mention(event, say):
    event_id = event.get("event_ts", "")
    if event_id in processed_events:
        return
    processed_events.add(event_id)

    text = event.get("text", "")
    question = text.split(">", 1)[-1].strip() if ">" in text else text.strip()

    if not question:
        say("質問を入力してください。\n例：`@manual-bot 貸会議室の使い方を教えて`")
        return

    say("🔍 検索中...少々お待ちください")

    manual_content = search_notion(question)
    if not manual_content:
        say("関連するマニュアルが見つかりませんでした。\nキーワードを変えて再度お試しください。")
        return

    try:
        answer = ask_claude(question, manual_content)
        say(answer)
    except Exception:
        say("⚠️ 回答の生成中にエラーが発生しました。少し時間をおいて再度お試しください。")


@app.event("message")
def handle_dm(event, say):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return
    if event.get("subtype"):
        return

    event_id = event.get("ts", "")
    if event_id in processed_events:
        return
    processed_events.add(event_id)

    question = event.get("text", "").strip()
    if not question:
        return

    say("🔍 検索中...少々お待ちください")

    manual_content = search_notion(question)
    if not manual_content:
        say("関連するマニュアルが見つかりませんでした。\nキーワードを変えて再度お試しください。")
        return

    try:
        answer = ask_claude(question, manual_content)
        say(answer)
    except Exception:
        say("⚠️ 回答の生成中にエラーが発生しました。少し時間をおいて再度お試しください。")


if __name__ == "__main__":
    print("マニュアルボット起動中...", flush=True)
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("Slackに接続完了！ DMまたは@manual-botで質問できます", flush=True)
    handler.start()
