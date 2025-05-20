# Micki'NAS Backend

This is the backend service for **Micki'NAS**, a secure, role-based internal file storage system developed as part of a Bachelor's thesis. The backend is built using **Flask** and serves a RESTful API to handle authentication, file management, access control, and audit logging.

## 🧩 Key Features

- 🔐 **JWT-based Authentication** with secure HTTP-only cookies
- 📁 **File Operations**: upload, download, rename, delete, folder creation
- 👥 **Role-Based Access Control**: Admin, Team Lead, Employee
- 📝 **Audit Logging** of user actions (e.g. downloads, uploads)
- 🌐 **REST API** architecture
- 🧱 **CouchDB** as the NoSQL data layer
- 🐳 Docker-friendly setup

## ⚙️ Technologies

- **Python 3.11+**
- **Flask**, **Flask-JWT-Extended**, **Flask-CORS**, **Flask-SocketIO**
- **CouchDB**
- **Docker**, **Nginx**, **Certbot** (for deployment)

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/Dovydas-Mickus/bachelors_backend.git
cd bachelors_backend
```

### 2. Create a Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```
### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Set Up Environment Variables
Create a .env file in the project root with:
```bash
JWT_SECRET_KEY=your_jwt_secret
COUCHDB_URL=http://localhost:5984
COUCHDB_USER=admin
COUCHDB_PASSWORD=password
```

### 5. Run the Backend Server
```bash
flask run
```
Or using a production-ready server (e.g., Gunicorn or Uvicorn with ASGI wrappers).

📦 requirements.txt
All dependencies are listed in requirements.txt. They include Flask, CouchDB client, cryptographic libraries, and various HTTP and API tools.

📁 Folder Structure
```bash
/
├── app/                  # Flask application files
│   ├── routes/
│   ├── services/
│   ├── utils/
│   └── ...
├── .env
├── requirements.txt
└── app.py                # Entry point\
```

🧪 Testing
You can use tools like Postman or Curl to test API endpoints. Consider writing pytest unit tests for expanded coverage.

🔐 Security Notes
- JWT tokens are stored in secure, HTTP-only cookies.
- All file access is role-restricted and audited.
- HTTPS should be enforced in production (use Nginx + Let's Encrypt).

