# Telegram File Sharing Bot 🔗📁

An advanced Telegram bot with deep linking support for secure file sharing, now with enhanced features.

## Features ✨
- **Deep linking** with unique, auto-generated codes
- **Multi-file support** (photos, videos, documents, audio)
- **Owner controls** for content management
- **Batch uploading** with `/savefiles`
- **Broadcast system** for announcements
- **Uptime monitoring** with `/uptime`
- **User statistics** tracking

## Enhanced Features 🚀
```diff
+ File management system (/viewfiles, /deletefiles)
+ Media batch uploading
+ Broadcast messages with media support
+ User authorization system
+ SQLite database for persistent storage
+ Uptime tracking
```

## Installation ⚙️
1. Clone the repository:
   ```sh
   git clone https://github.com/your-username/telegram-deep-linking-bot.git
   cd telegram-deep-linking-bot
   ```

2. Configure the bot:
   - Edit `config.py`:
     ```python
     TOKEN = "YOUR_BOT_TOKEN"  # Get from @BotFather
     AUTHORIZED_USERS = {5647525608, 1764307921}  # Add your user IDs
     ```

3. Install dependencies:
   ```sh
   pip install python-telegram-bot sqlite3
   ```

4. Run the bot:
   ```sh
   python bot.py
   ```

## Command Reference 📋
| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Welcome message with deep link support | All |
| `/help` | Show command list | All |
| `/savefiles` | Save uploaded files and generate link | Owner |
| `/viewfiles` | List uploaded files with codes | Owner |
| `/deletefiles [code]` | Delete specific file batch | Owner |
| `/broadcast` | Send message to all users | Owner |
| `/stats` | Show bot statistics | Owner |
| `/uptime` | Display bot running time | Owner |
| `/cancelupload` | Cancel current upload session | Owner |

## Deep Linking Usage 🔗
1. Upload files with captions (if desired)
2. Use `/savefiles` to generate shareable link
3. Share the format:  
   `https://t.me/yourbotname?start=UNIQUE_CODE`

## Database Structure 💾
The bot uses SQLite with these tables:
- `users` - Tracks user IDs and usernames
- `files` - Stores file metadata and share codes
- `temp_files` - Temporary upload storage
