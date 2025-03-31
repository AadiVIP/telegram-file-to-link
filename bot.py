import sqlite3
import random
import string
import time
import asyncio
from telegram import Update, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Database Setup
conn = sqlite3.connect("files.db", check_same_thread=False)
cursor = conn.cursor()

def column_exists(table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    for column in columns:
        if column[1] == column_name:
            return True
    return False

# Initialize database tables
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)")
cursor.execute("""CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    file_id TEXT,
    code TEXT,
    user_id INTEGER,
    file_type TEXT,
    caption TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)""")
cursor.execute("CREATE TABLE IF NOT EXISTS temp_files (user_id INTEGER, file_id TEXT, file_type TEXT, caption TEXT)")
conn.commit()

# Replace with your actual user ID and your friends' user IDs
AUTHORIZED_USERS = {
    1234567890,  # Your user ID
    2345678901,  # Friend 1
    3456789012,  # Friend 2
    4567890123,  # Friend 3
    5678901234   # Friend 3
}

START_TIME = time.time()

def get_uptime():
    seconds = int(time.time() - START_TIME)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m {seconds}s"

def generate_code():
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))

def is_authorized(user_id):
    return user_id in AUTHORIZED_USERS

async def file_handler(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to upload files.")
        return

    file = None
    file_type = ""
    caption = update.message.caption
    user_id = update.message.from_user.id

    # Initialize batch tracking if not exists
    if 'file_batch_count' not in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.photo:
        file = update.message.photo[-1]
        file_type = "photo"
    elif update.message.audio:
        file = update.message.audio
        file_type = "audio"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    elif update.message.voice:
        file = update.message.voice
        file_type = "voice"
    elif update.message.video_note:
        file = update.message.video_note
        file_type = "video_note"
    elif update.message.animation:
        file = update.message.animation
        file_type = "animation"
    elif update.message.sticker:
        file = update.message.sticker
        file_type = "sticker"
    else:
        return

    file_id = file.file_id
    
    try:
        # Test if the file is accessible
        file_obj = await context.bot.get_file(file_id)
        await file_obj.download_to_drive()  # or just check if it exists
    except Exception as e:
        await update.message.reply_text("⚠️ Error: This file appears to be invalid or inaccessible. Please resend it.")
        return

    cursor.execute(
        "INSERT INTO temp_files (user_id, file_id, file_type, caption) VALUES (?, ?, ?, ?)",
        (user_id, file_id, file_type, caption)
    )
    conn.commit()
    
    # Update batch counter
    context.user_data['file_batch_count'] += 1
    current_time = time.time()

    # Count total pending files
    cursor.execute("SELECT COUNT(*) FROM temp_files WHERE user_id=?", (user_id,))
    total_files = cursor.fetchone()[0]

    # Message based on file count
    if total_files < 10:
        await update.message.reply_text("📥 Files received. Use /savefiles when done.")
    else:
        await update.message.reply_text(f"📥 Received {total_files} files in this batch. Use /savefiles when ready.")


async def save_files(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to save files.")
        return

    user_id = update.message.from_user.id
    code = generate_code()
    total_saved = 0
    
    # Process all files at once (no auto-saving)
    cursor.execute("SELECT file_id, file_type, caption FROM temp_files WHERE user_id=?", (user_id,))
    files = cursor.fetchall()
    
    if not files:
        await update.message.reply_text("📭 No files found! Please upload files first.")
        return

    for file_entry in files:
        file_id, file_type, caption = file_entry
        cursor.execute(
            "INSERT INTO files (file_id, code, user_id, file_type, caption) VALUES (?, ?, ?, ?, ?)",
            (file_id, code, user_id, file_type, caption)
        )
        total_saved += 1
    
    # Clear temp files
    cursor.execute("DELETE FROM temp_files WHERE user_id=?", (user_id,))
    conn.commit()
    
    # Reset batch counter
    if 'file_batch_count' in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    deep_link = f"https://t.me/{context.bot.username}?start={code}"
    await update.message.reply_text(
        f"💾 Successfully saved {total_saved} files!\n"
        f"🔗 Share link: <code>{deep_link}</code>\n"
        f"🆔 Code: <code>{code}</code>",
        parse_mode='HTML'
    )

# [Rest of your code remains exactly the same - start, delete_files, view_files, stats, broadcast, uptime, main]

async def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (user.id, user.username)
    )
    conn.commit()

    if context.args:
        code = context.args[0]
        cursor.execute("SELECT file_id, file_type, caption FROM files WHERE code=?", (code,))
        results = cursor.fetchall()

        if not results:
            await update.message.reply_text("🔍 Invalid or expired link.")
            return

        # Group files into media batches
        media_groups = []
        current_group = []
        current_group_type = None
        groupable_types = {'photo', 'video', 'document', 'audio'}

        for file_entry in results:
            file_id, file_type, caption = file_entry

            if file_type in groupable_types:
                if file_type == current_group_type and len(current_group) < 10:
                    current_group.append((file_type, file_id, caption))
                else:
                    if current_group:
                        media_groups.append(current_group)
                    current_group = [(file_type, file_id, caption)]
                    current_group_type = file_type
            else:
                if current_group:
                    media_groups.append(current_group)
                    current_group = []
                    current_group_type = None
                media_groups.append([(file_type, file_id, caption)])

        if current_group:
            media_groups.append(current_group)

        # Send with retry logic
        max_retries = 3
        for group in media_groups:
            for attempt in range(max_retries):
                try:
                    if len(group) > 1:
                        media = []
                        for idx, (ftype, fid, cap) in enumerate(group):
                            if ftype == 'photo':
                                media.append(InputMediaPhoto(fid, caption=cap if idx == 0 else None))
                            elif ftype == 'video':
                                media.append(InputMediaVideo(fid, caption=cap if idx == 0 else None))
                            elif ftype == 'document':
                                media.append(InputMediaDocument(fid, caption=cap if idx == 0 else None))
                            elif ftype == 'audio':
                                media.append(InputMediaDocument(fid, caption=cap if idx == 0 else None))
                        
                        await update.message.reply_media_group(media=media, write_timeout=30)
                    else:
                        ftype, fid, cap = group[0]
                        send_methods = {
                            'photo': update.message.reply_photo,
                            'audio': update.message.reply_audio,
                            'video': update.message.reply_video,
                            'voice': update.message.reply_voice,
                            'video_note': update.message.reply_video_note,
                            'animation': update.message.reply_animation,
                            'sticker': update.message.reply_sticker
                        }
                        method = send_methods.get(ftype, update.message.reply_document)
                        await method(fid, caption=cap, write_timeout=20)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"Retrying {attempt + 1}/{max_retries} - Error: {str(e)}")
                        await asyncio.sleep(2)
                    else:
                        await update.message.reply_text(f"⌛ Failed to send files after multiple attempts. Error: {str(e)}")
                        continue
    else:
        # Welcome message when no arguments are provided
        await update.message.reply_text(
            "🌟 Welcome to the File Sharing Bot! 🌟\n\n"
            "📤 To upload files:\n"
            "1. Send me any files (photos, videos, documents, etc.)\n"
            "2. Use /savefiles when done to get a shareable link\n\n"
            "📥 To download files:\n"
            "• Click on any shared link from this bot\n\n"
            "🔧 Other commands:\n"
            "/viewfiles - See your uploaded files\n"
            "/deletefiles [code] - Delete a file batch\n"
            "/cancelupload - Cancel current upload session\n\n"
            "🚀 Start by sending me some files!",
            parse_mode='HTML'
        )

async def help_command(update: Update, context: CallbackContext):
    help_text = """
<b>📚 Bot Command Guide</b>

<b>👋 General Commands:</b>
/start - Welcome message and instructions
/help - Show this help message

<b>📤 Upload Commands:</b>
/savefiles - Save uploaded files and generate link
/cancelupload - Cancel current upload session

<b>🗂 File Management:</b>
/viewfiles - View your uploaded files with codes
/deletefiles [code] - Delete files using their code

<b>⚙️ Admin Tools:</b>
/stats - View bot statistics
/broadcast - Send message to all users
/uptime - Show bot running time

<b>🔄 How to Use:</b>
1. Send files (photos, videos, documents etc)
2. Use /savefiles to get shareable link
3. Share the link with anyone
"""

    if is_authorized(update.message.from_user.id):
        # Show admin commands to authorized users
        await update.message.reply_text(help_text, parse_mode='HTML')
    else:
        # Show basic commands to regular users
        basic_help = """
<b>📚 Available Commands:</b>
/start - Welcome message
/help - Show this help

<b>🔄 How to Use:</b>
• Click shared links to download files
• Contact owner for upload access
"""
        await update.message.reply_text(basic_help, parse_mode='HTML')

async def delete_files(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You are not authorized to delete files.")
        return

    if not context.args:
        await update.message.reply_text("ℹ Usage: /deletefiles <code>")
        return
    
    code = context.args[0]
    
    # Check if user is owner of these files
    cursor.execute("SELECT COUNT(*) FROM files WHERE code=? AND user_id=?", (code, user_id))
    count = cursor.fetchone()[0]
    if count == 0:
        await update.message.reply_text("❌ Either the code is invalid or you don't own these files.")
        return
    
    cursor.execute("DELETE FROM files WHERE code=? AND user_id=?", (code, user_id))
    conn.commit()
    await update.message.reply_text("🗑️ Files successfully deleted!")

async def view_files(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You are not authorized to view files.")
        return

    cursor.execute("""
        SELECT code, caption, file_type, COUNT(*) as file_count
        FROM files 
        WHERE user_id=?
        GROUP BY code
        ORDER BY MAX(timestamp) DESC
        LIMIT 50  -- Limit to 50 most recent batches
    """, (user_id,))
    results = cursor.fetchall()
    
    if not results:
        await update.message.reply_text("📭 Your file vault is empty!")
        return

    type_emojis = {
        'video': '🎬',
        'document': '📄',
        'photo': '🖼️',
        'audio': '🎵',
        'voice': '🎤',
        'animation': '🎞️',
        'sticker': '🩹'
    }

    response = "✨ <b>Your File Vault</b> ✨\n\n"
    for code, caption, file_type, file_count in results:
        emoji = type_emojis.get(file_type, '📁')
        filename = (caption.split('\n')[0][:50] + '...') if caption else f"Unnamed {file_type}"
        
        response += (
            f"{emoji} <b>{filename}</b>\n"
            f"   📂 Files: <code>{file_count}</code>\n"
            f"   🔗 <code>https://t.me/{context.bot.username}?start={code}</code>\n"
            f"   🆔 <code>{code}</code>\n\n"
        )

    total_files = sum(row[3] for row in results)
    total_links = len(results)
    response += f"📊 <i>Showing {total_links} most recent batches ({total_files} files total)</i>"

    await update.message.reply_text(
        response, 
        parse_mode='HTML',
        disable_web_page_preview=True
    )

async def stats(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to view statistics.")
        return

    cursor.execute("SELECT COUNT(*) FROM files")
    total_files = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT code) FROM files")
    total_links = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    response = (
        f"📈 <b>Bot Statistics</b>\n\n"
        f"• 📦 Total Files: <code>{total_files}</code>\n"
        f"• 🔗 Total Share Links: <code>{total_links}</code>\n"
        f"• 👥 Total Users: <code>{total_users}</code>\n"
        f"• ⏱ Uptime: <code>{get_uptime()}</code>"
    )
    await update.message.reply_text(response, parse_mode='HTML')

async def cancel_upload(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You are not authorized to cancel uploads.")
        return

    cursor.execute("DELETE FROM temp_files WHERE user_id=?", (user_id,))
    conn.commit()

    # Reset batch counter
    if 'file_batch_count' in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    await update.message.reply_text("❌ Your pending file uploads have been canceled.")


async def broadcast(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to broadcast messages.")
        return

    # Check if we're confirming a pending broadcast
    if update.message.text == '/broadcast_confirm':
        if 'pending_broadcast' not in context.user_data:
            await update.message.reply_text("⚠️ No pending broadcast to confirm.")
            return
            
        original_msg = await context.bot.get_message(
            chat_id=update.message.chat_id,
            message_id=context.user_data['pending_broadcast']
        )
        users = cursor.execute("SELECT user_id FROM users").fetchall()
        await start_broadcast_task(update, context, original_msg, users)
        return

    # Normal broadcast command
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "ℹ How to broadcast:\n\n"
            "1. Send the content you want to broadcast (text, photo, video, etc.)\n"
            "2. Reply to that message with /broadcast\n\n"
            "The bot will forward your exact message to all users."
        )
        return

    users = cursor.execute("SELECT user_id FROM users").fetchall()
    original_msg = update.message.reply_to_message

    # For large broadcasts, require confirmation
    if len(users) > 50:
        context.user_data['pending_broadcast'] = original_msg.message_id
        await update.message.reply_text(
            f"⚠️ This will broadcast to {len(users)} users. "
            f"Confirm with /broadcast_confirm or cancel by ignoring."
        )
        return

    await start_broadcast_task(update, context, original_msg, users)

async def start_broadcast_task(update: Update, context: CallbackContext, original_msg, users):
    # Create a progress message
    progress_msg = await update.message.reply_text(
        "📢 Starting broadcast...\n"
        "⏳ Progress: 0%\n"
        "✅ Success: 0\n"
        "❌ Failed: 0"
    )

    success = 0
    failed = 0
    total_users = len(users)
    start_time = time.time()

    for index, (user_id,) in enumerate(users):
        try:
            # Rate limiting
            if index > 0 and index % 25 == 0:
                await asyncio.sleep(1)

            # Update progress every 10 messages or 10%
            if index % 10 == 0 or index == total_users - 1:
                progress = int((index + 1) / total_users * 100)
                await context.bot.edit_message_text(
                    chat_id=progress_msg.chat_id,
                    message_id=progress_msg.message_id,
                    text=(
                        f"📢 Broadcasting...\n"
                        f"⏳ Progress: {progress}%\n"
                        f"✅ Success: {success}\n"
                        f"❌ Failed: {failed}\n"
                        f"⏱ Elapsed: {int(time.time() - start_time)}s"
                    )
                )

            # Send the appropriate message type
            if original_msg.text:
                await context.bot.send_message(chat_id=user_id, text=original_msg.text)
            elif original_msg.photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=original_msg.photo[-1].file_id,
                    caption=original_msg.caption
                )
            elif original_msg.video:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=original_msg.video.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.document:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=original_msg.document.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.audio:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=original_msg.audio.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.voice:
                await context.bot.send_voice(chat_id=user_id, voice=original_msg.voice.file_id)
            elif original_msg.animation:
                await context.bot.send_animation(
                    chat_id=user_id,
                    animation=original_msg.animation.file_id,
                    caption=original_msg.caption
                )
            else:
                failed += 1
                continue
                
            success += 1
        except Exception as e:
            print(f"Failed to send to {user_id}: {e}")
            failed += 1
            continue

    # Final report
    elapsed_time = int(time.time() - start_time)
    await context.bot.edit_message_text(
        chat_id=progress_msg.chat_id,
        message_id=progress_msg.message_id,
        text=(
            f"📢 <b>Broadcast Complete</b>\n\n"
            f"✅ Success: <code>{success}</code>\n"
            f"❌ Failed: <code>{failed}</code>\n"
            f"📊 Total Users: <code>{total_users}</code>\n"
            f"⏱ Elapsed Time: <code>{elapsed_time}s</code>\n\n"
            f"{(success/total_users*100):.1f}% delivery success rate"
        ),
        parse_mode='HTML'
    )

    # Clean up
    if 'pending_broadcast' in context.user_data:
        del context.user_data['pending_broadcast']

async def uptime(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to view uptime.")
        return
    await update.message.reply_text(f"⏱ <b>Bot Uptime:</b> <code>{get_uptime()}</code>", parse_mode='HTML')

def main():
    TOKEN = "YOUR BOT TOKEN HERE"
    
    print("💖 Starting bot...")
    app = Application.builder().token(TOKEN)\
        .read_timeout(30)\
        .connect_timeout(30)\
        .pool_timeout(30)\
        .build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("savefiles", save_files))
    app.add_handler(CommandHandler("deletefiles", delete_files))
    app.add_handler(CommandHandler("viewfiles", view_files))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("cancelupload", cancel_upload))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("help", help_command))

    # File handler
    file_filter = (filters.Document.ALL | filters.PHOTO | filters.AUDIO |
                  filters.VIDEO | filters.VOICE | filters.VIDEO_NOTE |
                  filters.ANIMATION | filters.Sticker.ALL)
    app.add_handler(MessageHandler(file_filter, file_handler))

    print("💖 Your bot is ready, my king!")
    app.run_polling(
        poll_interval=3,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
