#!/bin/bash
# -----------------------------------------------------------------------------
# OCEANWAVES POS - VM Setup Script (Oracle Free Tier / 1GB RAM)
# -----------------------------------------------------------------------------
set -e

echo "🚀 Starting VM Optimization for 1GB RAM..."

# 1. SWAP MEMORY (Mandatory for 1GB RAM)
if [ ! -f /swapfile ]; then
    echo "💾 Creating 2GB Swap Memory..."
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# 2. INSTALL DEPENDENCIES
echo "📦 Installing System Packages..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv nginx mysql-server libmysqlclient-dev pkg-config

# 3. TUNE MYSQL (Lean config for 1GB RAM)
echo "🛢 Tuning MySQL for Low Resources..."
MYSQL_CONF="/etc/mysql/mysql.conf.d/mysqld.cnf"
sudo sed -i "s/.*innodb_buffer_pool_size.*/innodb_buffer_pool_size = 128M/" $MYSQL_CONF || echo "innodb_buffer_pool_size = 128M" | sudo tee -a $MYSQL_CONF
sudo sed -i "s/.*max_connections.*/max_connections = 50/" $MYSQL_CONF || echo "max_connections = 50" | sudo tee -a $MYSQL_CONF
sudo systemctl restart mysql

# 4. APP SETUP
echo "🐍 Setting up Python Environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. DATABASE INITIALIZATION
if [ -f .env ]; then
    echo "⚡ Setting up MySQL Database..."
    # Extract DB details from .env
    DB_NAME=$(grep DB_NAME .env | cut -d '=' -f2)
    DB_USER=$(grep DB_USER .env | cut -d '=' -f2)
    DB_PASS=$(grep DB_PASSWORD .env | cut -d '=' -f2)

    sudo mysql -u root -e "CREATE DATABASE IF NOT EXISTS $DB_NAME;"
    sudo mysql -u root -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';"
    sudo mysql -u root -e "GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';"
    sudo mysql -u root -e "FLUSH PRIVILEGES;"

    echo "⚡ Running Django Migrations..."
    python manage.py migrate
    python manage.py collectstatic --noinput
fi

echo "✅ VM Setup Complete! Next steps:"
echo "1. Update your .env with the domain name"
echo "2. Run Gunicorn using 'gunicorn -c gunicorn.conf.py oceanwaves_project.wsgi'"
