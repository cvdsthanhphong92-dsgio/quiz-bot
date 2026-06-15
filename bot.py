import os
import io
import base64
import csv
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "")


def normalize(s):
    import unicodedata
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "d")
    return " ".join(s.split())


def fetch_and_dedup_sheet():
    resp = requests.get(SHEET_CSV_URL, timeout=15)
    resp.encoding = "utf-8"
    text = resp.text
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    seen = set()
    unique = []
    for row in rows[1:]:
        if len(row) < 5:
            continue
        q_raw = row[1]
        correct = row[4].strip()
        q_text = q_raw.split("/", 1)[1].strip() if "/" in q_raw else q_raw
        key = normalize(q_text) + "||" + normalize(correct)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def search_rows(rows, raw_input):
    has_split = "..." in raw_input
    if has_split:
        segments = [normalize(s) for s in raw_input.split("...") if s.strip()]
    else:
        segments = None
        terms = normalize(raw_input).split()

    results = []
    for row in rows:
        q_raw = row[1]
        cat = "Khác"
        q_text = q_raw
        if "/" in q_raw:
            cat = q_raw.split("/")[0].strip()
            q_text = q_raw.split("/", 1)[1].strip()
        opts_raw = row[2]
        correct = row[4].strip()
        haystack = normalize(q_text + " " + opts_raw + " " + correct)

        if has_split:
            pos = 0
            matched = True
            for seg in segments:
                idx = haystack.find(seg, pos)
                if idx == -1:
                    matched = False
                    break
                pos = idx + len(seg)
        else:
            matched = len(terms) > 0 and all(t in haystack for t in terms)

        if matched:
            results.append({
                "stt": row[0],
                "cat": cat,
                "q_text": q_text,
                "opts_raw": opts_raw,
                "correct": correct,
            })
    return results


def format_results(results, raw_input):
    total = len(results)
    show_max = 3
    shown = results[:show_max]

    if total == 0:
        return f'❌ Không tìm thấy câu hỏi nào khớp với "{raw_input}".\nVui lòng thử từ khoá khác nhé!'

    msg = f"🔍 Tìm thấy {total} câu hỏi"
    msg += f" (hiển thị {show_max} câu đầu):\n" if total > show_max else ":\n"

    for q in shown:
        import re
        opts = re.split(r";\s*(?=[A-D]:)", q["opts_raw"])
        opts = [o.strip() for o in opts if o.strip()]
        msg += "\n━━━━━━━━━━━━━━━\n"
        msg += f"📌 [{q['cat']}] Câu {q['stt']}\n"
        msg += q["q_text"] + "\n\n"
        for opt in opts:
            norm_opt = normalize(re.sub(r"^[A-D]:\s*", "", opt))
            norm_ans = normalize(q["correct"])
            is_correct = norm_ans in norm_opt or norm_opt in norm_ans
            msg += ("✅ " if is_correct else "   ") + opt + "\n"
        msg += f"\n🎯 Đáp án đúng: {q['correct']}\n"

    if total > show_max:
        msg += f"\n━━━━━━━━━━━━━━━\n📎 ...và {total - show_max} câu khác. Thu hẹp từ khoá để xem chính xác hơn."

    return msg


def ocr_image(photo_bytes, mime_type="image/jpeg"):
    b64 = base64.b64encode(photo_bytes).decode()
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                {"type": "text", "text": "Đọc nội dung chữ trong ảnh này. Chỉ trả về text thuần tuý, không giải thích thêm."}
            ]
        }]
    }
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    resp = requests.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers, timeout=30)
    data = resp.json()
    return data["content"][0]["text"]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if msg.photo:
        await msg.reply_text("📷 Đang đọc ảnh, vui lòng chờ...")
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        ocr_text = ocr_image(bytes(photo_bytes))
        rows = fetch_and_dedup_sheet()
        results = search_rows(rows, ocr_text)
        reply = format_results(results, ocr_text)
        await msg.reply_text(reply)

    elif msg.text:
        text = msg.text.strip()
        if text.startswith("/start"):
            await msg.reply_text(
                "👋 Xin chào! Tôi là Quiz Bot tra cứu câu hỏi kiểm tra.\n\n"
                "📝 *Cách dùng:*\n"
                "• Gõ từ khoá để tìm câu hỏi\n"
                "• Dùng `...` để tìm theo thứ tự: `buddy...thành công`\n"
                "• Gửi ảnh chụp câu hỏi để tìm tự động\n\n"
                "Thử gõ một từ khoá nhé!",
                parse_mode="Markdown"
            )
            return
        rows = fetch_and_dedup_sheet()
        results = search_rows(rows, text)
        reply = format_results(results, text)
        await msg.reply_text(reply)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    print("Bot đang chạy...")
    app.run_polling()


if __name__ == "__main__":
    main()
