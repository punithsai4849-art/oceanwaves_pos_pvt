import smtplib
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

def send_backup(file_path):
    # 1. Load configuration from .env
    from pathlib import Path
    env_path = Path(__file__).parent.parent / '.env'
    
    config = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    config[k] = v
    else:
        print("Error: .env file not found.")
        sys.exit(1)

    # 2. Extract email settings
    smtp_user = config.get('EMAIL_HOST_USER')
    smtp_pass = config.get('EMAIL_HOST_PASSWORD')
    smtp_port = 587  # Standard TLS port for Gmail
    smtp_host = 'smtp.gmail.com'
    recipient = config.get('ADMIN_NOTIFICATION_EMAIL', smtp_user)

    if not all([smtp_user, smtp_pass]):
        print("Error: SMTP credentials missing in .env")
        sys.exit(1)

    # 3. Create Message
    msg = MIMEMultipart()
    msg['From'] = smtp_user
    msg['To'] = recipient
    msg['Subject'] = f"OCEANWAVES Weekly Backup - {datetime.now().strftime('%d %b %Y')}"

    body = f"Attached is the encrypted database backup for OCEANWAVES.\n\nDate: {datetime.now()}\nPassword: [As configured]\n\nThis is an automated message."
    from email.mime.text import MIMEText
    msg.attach(MIMEText(body, 'plain'))

    # 4. Attach File
    try:
        with open(file_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename= {os.path.basename(file_path)}",
            )
            msg.attach(part)
    except Exception as e:
        print(f"Error attaching file: {e}")
        sys.exit(1)

    # 5. Send via SMTP
    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully.")
    except Exception as e:
        print(f"SMTP Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 send_backup.py <file_path>")
        sys.exit(1)
    
    send_backup(sys.argv[1])
