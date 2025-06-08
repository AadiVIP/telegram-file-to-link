import os
import random
import string
import time
import asyncio
from pymongo import MongoClient
from telegram import Update, InputMediaDocument, InputMediaPhoto, InputMediaVideo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.error import BadRequest
from datetime import datetime, timedelta

# MongoDB Setup
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["file_sharing_bot"]

# Collections
users_col = db["users"]
files_col = db["files"]
temp_files_col = db["temp_files"]
config_col = db["global_config"]

# Initialize global config if not exists
if config_col.count_documents({"_id": "global_config"}) == 0:
    config_col.insert_one({
        "_id": "global_config",
        "default_auto_delete": False,
        "default_delete_after_hours": 24,
        "default_protect_content": False
    })

AUTHORIZED_USERS = {
    5647525608,  # Your user ID
    1764307921,  # Friend 1
    2025395515,  # Friend 2
    7238049840,
    286469410   # Friend 3
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

def get_global_config():
    config = config_col.find_one({"_id": "global_config"})
    return (
        config["default_auto_delete"],
        config["default_delete_after_hours"],
        config["default_protect_content"]
    )

def update_global_config(auto_delete=None, delete_after_hours=None, protect_content=None):
    update_data = {}
    if auto_delete is not None:
        update_data["default_auto_delete"] = auto_delete
    if delete_after_hours is not None:
        update_data["default_delete_after_hours"] = delete_after_hours
    if protect_content is not None:
        update_data["default_protect_content"] = protect_content
    
    if update_data:
        config_col.update_one(
            {"_id": "global_config"},
            {"$set": update_data}
        )

def get_code_config(code):
    file = files_col.find_one({"code": code})
    if file:
        return (
            file.get("auto_delete", False),
            file.get("delete_after_hours", 24),
            file.get("protect_content", False)
        )
    return None

def update_code_config(code, auto_delete=None, delete_after_hours=None, protect_content=None):
    update_data = {}
    if auto_delete is not None:
        update_data["auto_delete"] = auto_delete
    if delete_after_hours is not None:
        update_data["delete_after_hours"] = delete_after_hours
    if protect_content is not None:
        update_data["protect_content"] = protect_content
    
    if update_data:
        files_col.update_many(
            {"code": code},
            {"$set": update_data}
        )

async def file_handler(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to upload files.")
        return

    file = None
    file_type = ""
    caption = update.message.caption
    user_id = update.message.from_user.id

    if 'file_batch_count' not in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    is_forwarded = hasattr(update.message, 'forward_origin')
    print(f"New message received - Is forwarded: {is_forwarded}")

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
    print(f"Processing {file_type} file - ID: {file_id}")

    try:
        if not is_forwarded:
            print("Not a forwarded message - attempting download...")
            file_obj = await context.bot.get_file(file_id)
            await file_obj.download_to_drive()
            print("Download successful")
        else:
            print("Forwarded message - skipping download verification")
    except Exception as e:
        print(f"Error processing file: {str(e)}")
        await update.message.reply_text("⚠️ Error: This file appears to be invalid or inaccessible. Please resend it.")
        return

    temp_files_col.insert_one({
        "user_id": user_id,
        "file_id": file_id,
        "file_type": file_type,
        "caption": caption,
        "timestamp": datetime.now()
    })
    
    user_id = update.message.from_user.id

    # Cancel any existing notification jobs for this user
    current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
    for job in current_jobs:
        job.schedule_removal()

    # Schedule a new delayed notification (3 seconds)
    context.job_queue.run_once(
        callback=send_final_notification,
        when=3,  # 3-second delay
        chat_id=update.message.chat_id,
        user_id=user_id,
        name=str(user_id)  # Unique identifier for the job
    )

async def send_final_notification(context: CallbackContext):
    job = context.job
    user_id = job.user_id
    chat_id = job.chat_id

    total_files = temp_files_col.count_documents({"user_id": user_id})

    if total_files < 10:
        msg = "📥 Files received. Use /savefiles when done."
    else:
        msg = f"📥 Received {total_files} files in this batch. Use /savefiles when ready."

    await context.bot.send_message(chat_id=chat_id, text=msg)

async def save_files(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to save files.")
        return

    user_id = update.message.from_user.id
    code = generate_code()
    total_saved = 0
    
    default_auto_delete, default_delete_after, default_protect = get_global_config()
    delete_time = None
    if default_auto_delete:
        delete_time = datetime.now() + timedelta(hours=default_delete_after)
    
    files = list(temp_files_col.find({"user_id": user_id}))
    
    if not files:
        await update.message.reply_text("📭 No files found! Please upload files first.")
        return

    file_docs = []
    for file_entry in files:
        file_doc = {
            "file_id": file_entry["file_id"],
            "code": code,
            "user_id": user_id,
            "file_type": file_entry["file_type"],
            "caption": file_entry["caption"],
            "timestamp": datetime.now(),
            "auto_delete": default_auto_delete,
            "delete_after_hours": default_delete_after,
            "protect_content": default_protect
        }
        if delete_time:
            file_doc["delete_time"] = delete_time
        file_docs.append(file_doc)
    
    if file_docs:
        files_col.insert_many(file_docs)
        total_saved = len(file_docs)
    
    temp_files_col.delete_many({"user_id": user_id})
    
    if 'file_batch_count' in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    deep_link = f"https://t.me/{context.bot.username}?start={code}"
    
    auto_delete_info = ""
    if default_auto_delete:
        auto_delete_info = f"\n⏳ Files will auto-delete after {default_delete_after} hours."

    response = (
        f"💾 Successfully saved {total_saved} files!\n"
        f"🔗 Share link: <code>{deep_link}</code>\n"
        f"🆔 Code: <code>{code}</code>"
        f"{auto_delete_info}"
    )
    
    await update.message.reply_text(response, parse_mode='HTML')

async def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    users_col.update_one(
        {"user_id": user.id},
        {"$set": {"username": user.username}},
        upsert=True
    )

    if context.args:
        code = context.args[0]
        results = list(files_col.find({"code": code}))
        
        if not results:
            await update.message.reply_text("🔍 Invalid or expired link.")
            return

        media_groups = []
        current_group = []
        current_group_type = None
        groupable_types = {'photo', 'video', 'document', 'audio'}
        protect_content = results[0].get("protect_content", False) if results else False

        for file_entry in results:
            file_type = file_entry["file_type"]
            file_id = file_entry["file_id"]
            caption = file_entry.get("caption")

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
                        
                        await update.message.reply_media_group(
                            media=media, 
                            write_timeout=30,
                            protect_content=protect_content
                        )
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
                        await method(
                            fid, 
                            caption=cap, 
                            write_timeout=20,
                            protect_content=protect_content
                        )
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"Retrying {attempt + 1}/{max_retries} - Error: {str(e)}")
                        await asyncio.sleep(2)
                    else:
                        await update.message.reply_text(f"⌛ Failed to send files after multiple attempts. Error: {str(e)}")
                        continue
    else:
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
            "/cancelupload - Cancel current upload session\n"
            "/config - Configure auto-delete settings\n\n"
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

<b>⚙️ Configuration:</b>
/config - Configure auto-delete settings
/config [code] - Configure specific file batch

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
        await update.message.reply_text(help_text, parse_mode='HTML')
    else:
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
    
    count = files_col.count_documents({"code": code, "user_id": user_id})
    if count == 0:
        await update.message.reply_text("❌ Either the code is invalid or you don't own these files.")
        return
    
    files_col.delete_many({"code": code, "user_id": user_id})
    await update.message.reply_text("🗑️ Files successfully deleted!")

async def view_files(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You are not authorized to view files.")
        return

    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$code",
            "caption": {"$first": "$caption"},
            "file_type": {"$first": "$file_type"},
            "file_count": {"$sum": 1},
            "auto_delete": {"$first": "$auto_delete"},
            "delete_after_hours": {"$first": "$delete_after_hours"},
            "protect_content": {"$first": "$protect_content"},
            "timestamp": {"$max": "$timestamp"}
        }},
        {"$sort": {"timestamp": -1}},
        {"$limit": 50}
    ]
    
    results = list(files_col.aggregate(pipeline))
    
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
    for result in results:
        code = result["_id"]
        file_type = result["file_type"]
        caption = result.get("caption")
        file_count = result["file_count"]
        auto_delete = result.get("auto_delete", False)
        delete_after = result.get("delete_after_hours", 24)
        protect_content = result.get("protect_content", False)
        
        emoji = type_emojis.get(file_type, '📁')
        filename = (caption.split('\n')[0][:50] + '...') if caption else f"Unnamed {file_type}"
        protection_emoji = '🔒' if protect_content else '🔓'
        auto_delete_info = "🔴 OFF" if not auto_delete else f"🟢 ON ({delete_after}h)"
        
        response += (
            f"{emoji} {protection_emoji} <b>{filename}</b>\n"
            f"   📂 Files: <code>{file_count}</code>\n"
            f"   🕒 Auto-delete: {auto_delete_info}\n"
            f"   🔗 <code>https://t.me/{context.bot.username}?start={code}</code>\n"
            f"   🆔 <code>{code}</code>\n\n"
        )

    total_files = sum(result["file_count"] for result in results)
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

    total_files = files_col.count_documents({})
    total_links = len(files_col.distinct("code"))
    total_users = users_col.count_documents({})

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

    temp_files_col.delete_many({"user_id": user_id})

    if 'file_batch_count' in context.user_data:
        context.user_data['file_batch_count'] = 0
        context.user_data['last_notification'] = 0

    await update.message.reply_text("❌ Your pending file uploads have been canceled.")

async def broadcast(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to broadcast messages.")
        return

    if update.message.text == '/broadcast_confirm':
        if 'pending_broadcast' not in context.user_data:
            await update.message.reply_text("⚠️ No pending broadcast to confirm.")
            return
            
        original_msg = await context.bot.get_message(
            chat_id=update.message.chat_id,
            message_id=context.user_data['pending_broadcast']
        )
        users = list(users_col.find({}, {"user_id": 1}))
        await start_broadcast_task(update, context, original_msg, users)
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "ℹ How to broadcast:\n\n"
            "1. Send the content you want to broadcast (text, photo, video, etc.)\n"
            "2. Reply to that message with /broadcast\n\n"
            "The bot will forward your exact message to all users."
        )
        return

    users = list(users_col.find({}, {"user_id": 1}))
    original_msg = update.message.reply_to_message

    if len(users) > 50:
        context.user_data['pending_broadcast'] = original_msg.message_id
        await update.message.reply_text(
            f"⚠️ This will broadcast to {len(users)} users. "
            f"Confirm with /broadcast_confirm or cancel by ignoring."
        )
        return

    await start_broadcast_task(update, context, original_msg, users)

async def start_broadcast_task(update: Update, context: CallbackContext, original_msg, users):
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

    for index, user in enumerate(users):
        try:
            if index > 0 and index % 25 == 0:
                await asyncio.sleep(1)

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

            if original_msg.text:
                await context.bot.send_message(chat_id=user["user_id"], text=original_msg.text)
            elif original_msg.photo:
                await context.bot.send_photo(
                    chat_id=user["user_id"],
                    photo=original_msg.photo[-1].file_id,
                    caption=original_msg.caption
                )
            elif original_msg.video:
                await context.bot.send_video(
                    chat_id=user["user_id"],
                    video=original_msg.video.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.document:
                await context.bot.send_document(
                    chat_id=user["user_id"],
                    document=original_msg.document.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.audio:
                await context.bot.send_audio(
                    chat_id=user["user_id"],
                    audio=original_msg.audio.file_id,
                    caption=original_msg.caption
                )
            elif original_msg.voice:
                await context.bot.send_voice(chat_id=user["user_id"], voice=original_msg.voice.file_id)
            elif original_msg.animation:
                await context.bot.send_animation(
                    chat_id=user["user_id"],
                    animation=original_msg.animation.file_id,
                    caption=original_msg.caption
                )
            else:
                failed += 1
                continue
                
            success += 1
        except Exception as e:
            print(f"Failed to send to {user['user_id']}: {e}")
            failed += 1
            continue

    elapsed_time = int(time.time() - start_time)
    await context.bot.edit_message_text(
        chat_id=progress_msg.chat_id,
        message_id=progress_msg.message_id,
        text=(
            f"📢 <b>Broadcast Complete</b>\n\n"
            f"✅ Success: <code>{success}</code>\n"
            f"❌ Failed: <code>{failed}</code>\n"
            f"?? Total Users: <code>{total_users}</code>\n"
            f"⏱ Elapsed Time: <code>{elapsed_time}s</code>\n\n"
            f"{(success/total_users*100):.1f}% delivery success rate"
        ),
        parse_mode='HTML'
    )

    if 'pending_broadcast' in context.user_data:
        del context.user_data['pending_broadcast']

async def uptime(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to view uptime.")
        return
    await update.message.reply_text(f"⏱ <b>Bot Uptime:</b> <code>{get_uptime()}</code>", parse_mode='HTML')

async def config_command(update: Update, context: CallbackContext):
    if not is_authorized(update.message.from_user.id):
        await update.message.reply_text("🚫 You are not authorized to configure settings.")
        return

    user_id = update.message.from_user.id
    
    if context.args:
        code = context.args[0]
        count = files_col.count_documents({"code": code, "user_id": user_id})
        if count == 0:
            await update.message.reply_text("❌ Invalid code or you don't own these files.")
            return
        
        config = get_code_config(code)
        if not config:
            config = get_global_config()
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data=f"code_toggle_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Delete after: {config[1]} hours",
                    callback_data=f"code_set_time_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data=f"code_protect_toggle_{code}"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"⚙️ <b>Configuration for code: {code}</b>\n\n"
            "Configure auto-delete settings for this specific file batch:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return
    
    config = get_global_config()
    
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔄 Default Auto-delete: {'ON' if config[0] else 'OFF'}",
                callback_data="global_toggle"
            )
        ],
        [
            InlineKeyboardButton(
                f"⏱ Default Delete after: {config[1]} hours",
                callback_data="global_set_time"
            )
        ],
        [
            InlineKeyboardButton(
                f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                callback_data="global_protect_toggle"
            )
        ],
        [InlineKeyboardButton("❌ Close", callback_data="config_close")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ <b>Global Configuration Settings</b>\n\n"
        "Configure default auto-delete settings for new files:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def config_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.edit_message_text("🚫 You are not authorized to configure settings.")
        return

    data = query.data
    
    if data == "config_close":
        await query.delete_message()
        return
        
    elif data.startswith("code_toggle_"):
        code = data[12:]
        current_setting, hours, protect = get_code_config(code)
        new_setting = not current_setting
        
        if new_setting != current_setting:
            update_code_config(code, auto_delete=new_setting)
            config = (new_setting, hours, protect)
        else:
            config = (current_setting, hours, protect)
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data=f"code_toggle_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Delete after: {config[1]} hours",
                    callback_data=f"code_set_time_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data=f"code_protect_toggle_{code}"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]
        
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except BadRequest:
            pass
        return
        
    elif data.startswith("code_set_time_"):
        code = data[14:]
        await query.edit_message_text(
            f"⏳ <b>Set auto-delete time for code: {code}</b>\n\n"
            "Send the number of hours after which these files should be automatically deleted (1-720):",
            parse_mode='HTML'
        )
        context.user_data['awaiting_code_time'] = code
        context.user_data['config_message_id'] = query.message.message_id
        return

    elif data.startswith("code_protect_toggle_"):
        code = data[20:]
        current_settings = get_code_config(code)
        new_protect = not current_settings[2]
        
        update_code_config(code, protect_content=new_protect)
        config = get_code_config(code)
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data=f"code_toggle_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Delete after: {config[1]} hours",
                    callback_data=f"code_set_time_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data=f"code_protect_toggle_{code}"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]
        
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest:
            pass

    elif data == "global_protect_toggle":
        current_settings = get_global_config()
        new_protect = not current_settings[2]
        
        update_global_config(protect_content=new_protect)
        config = get_global_config()
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Default Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data="global_toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Default Delete after: {config[1]} hours",
                    callback_data="global_set_time"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data="global_protect_toggle"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]
        
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest:
            pass

    elif data == "global_toggle":
        current_setting, hours, protect = get_global_config()
        new_setting = not current_setting
        
        if new_setting != current_setting:
            update_global_config(auto_delete=new_setting)
            config = (new_setting, hours, protect)
        else:
            config = (current_setting, hours, protect)
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Default Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data="global_toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Default Delete after: {config[1]} hours",
                    callback_data="global_set_time"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data="global_protect_toggle"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]
        
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except BadRequest:
            pass
        return
        
    elif data == "global_set_time":
        await query.edit_message_text(
            "⏳ <b>Set default auto-delete time</b>\n\n"
            "Send the number of hours after which new files should be automatically deleted (1-720):",
            parse_mode='HTML'
        )
        context.user_data['awaiting_global_time'] = True
        context.user_data['config_message_id'] = query.message.message_id
        return

async def handle_config_text(update: Update, context: CallbackContext):
    if 'awaiting_global_time' in context.user_data:
        try:
            hours = int(update.message.text)
            if not 1 <= hours <= 720:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid number between 1 and 720.")
            return

        update_global_config(delete_after_hours=hours)
        await update.message.delete()
        
        config_message_id = context.user_data['config_message_id']
        config = get_global_config()
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Default Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data="global_toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Default Delete after: {config[1]} hours",
                    callback_data="global_set_time"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data="global_protect_toggle"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]

        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=config_message_id,
            text="⚙️ <b>Global Configuration Settings</b>\n\n"
                 "Configure default auto-delete settings for new files:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        
        del context.user_data['awaiting_global_time']
        del context.user_data['config_message_id']
        
    elif 'awaiting_code_time' in context.user_data:
        try:
            hours = int(update.message.text)
            if not 1 <= hours <= 720:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid number between 1 and 720.")
            return

        code = context.user_data['awaiting_code_time']
        update_code_config(code, delete_after_hours=hours)
        await update.message.delete()
        
        config_message_id = context.user_data['config_message_id']
        config = get_code_config(code)
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔄 Auto-delete: {'ON' if config[0] else 'OFF'}",
                    callback_data=f"code_toggle_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⏱ Delete after: {config[1]} hours",
                    callback_data=f"code_set_time_{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔒 Content Protection: {'ON' if config[2] else 'OFF'}",
                    callback_data=f"code_protect_toggle_{code}"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="config_close")]
        ]

        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=config_message_id,
            text=f"⚙️ <b>Configuration for code: {code}</b>\n\n"
                 "Configure auto-delete settings for this specific file batch:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        
        del context.user_data['awaiting_code_time']
        del context.user_data['config_message_id']

async def error_handler(update: Update, context: CallbackContext) -> None:
    print(f"⚠️ Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ Oops! Something went wrong. Try again or notify the admin.")

async def check_auto_delete(context: CallbackContext):
    current_time = datetime.now()
    files_col.delete_many({
        "auto_delete": True,
        "$or": [
            {"delete_time": {"$lte": current_time}},
            {
                "$and": [
                    {"delete_time": {"$exists": False}},
                    {"$expr": {
                        "$lte": [
                            {"$add": ["$timestamp", {"$multiply": ["$delete_after_hours", 3600000]}]},
                            current_time
                        ]
                    }}
                ]
            }
        ]
    })

def main():
    TOKEN = "7709134991:AAFuzqJ_Cx8xI_3xSzPgMqy_1St2FiWoi8M"
    
    print("💖 Starting bot...")
    app = Application.builder().token(TOKEN)\
        .read_timeout(30)\
        .connect_timeout(30)\
        .pool_timeout(30)\
        .build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("savefiles", save_files))
    app.add_handler(CommandHandler("deletefiles", delete_files))
    app.add_handler(CommandHandler("viewfiles", view_files))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("cancelupload", cancel_upload))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("config", config_command))
    app.add_error_handler(error_handler)

    app.add_handler(CallbackQueryHandler(config_button, pattern="^code_|^global_|^config_"))
    
    file_filter = (filters.Document.ALL | filters.PHOTO | filters.AUDIO |
                  filters.VIDEO | filters.VOICE | filters.VIDEO_NOTE |
                  filters.ANIMATION | filters.Sticker.ALL)
    app.add_handler(MessageHandler(file_filter, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_config_text))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(check_auto_delete, interval=300, first=10)

    print("💖 Your bot is ready, my king!")
    app.run_polling(
        poll_interval=3,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()