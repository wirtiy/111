from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import json
import uuid
from datetime import date

ROOT = Path(__file__).parent
DATA = {
    "bookings": [],
    "messages": [],
}
RATES = {
    "Moon Dorm": 24,
    "Moon Double": 58,
    "Moon Family": 82,
}

class MoonLightHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Слишком большой запрос")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"status": "ok", "project": "Moon Light", "stage": 0})
        if path == "/api/bookings":
            return self.send_json(200, {"items": DATA["bookings"], "count": len(DATA["bookings"])})
        if path == "/api/messages":
            return self.send_json(200, {"items": DATA["messages"], "count": len(DATA["messages"])})
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.read_json()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return self.send_json(400, {"error": "Некорректный JSON"})

        if path == "/api/bookings":
            required = ["checkin", "checkout", "room", "name", "email"]
            if any(not str(data.get(key, "")).strip() for key in required):
                return self.send_json(400, {"error": "Заполните обязательные поля"})
            try:
                checkin = date.fromisoformat(data["checkin"])
                checkout = date.fromisoformat(data["checkout"])
                guests = int(data.get("guests", 1))
            except (ValueError, TypeError):
                return self.send_json(400, {"error": "Проверьте даты и количество гостей"})
            if checkout <= checkin:
                return self.send_json(400, {"error": "Дата выезда должна быть позже даты заезда"})
            if guests < 1 or guests > 4:
                return self.send_json(400, {"error": "Количество гостей должно быть от 1 до 4"})
            room = data["room"]
            if room not in RATES:
                return self.send_json(400, {"error": "Неизвестный тип номера"})
            nights = (checkout - checkin).days
            booking = {
                "id": str(uuid.uuid4())[:8],
                "checkin": checkin.isoformat(),
                "checkout": checkout.isoformat(),
                "nights": nights,
                "guests": guests,
                "room": room,
                "name": str(data["name"]).strip(),
                "email": str(data["email"]).strip(),
                "note": str(data.get("note", "")).strip(),
                "total": nights * RATES[room],
                "status": "new",
            }
            DATA["bookings"].append(booking)
            return self.send_json(201, {"message": "Заявка сохранена", "booking": booking})

        if path == "/api/messages":
            name = str(data.get("name", "")).strip()
            email = str(data.get("email", "")).strip()
            message = str(data.get("message", "")).strip()
            if not name or not email or not message:
                return self.send_json(400, {"error": "Заполните все поля сообщения"})
            item = {"id": str(uuid.uuid4())[:8], "name": name, "email": email, "message": message}
            DATA["messages"].append(item)
            return self.send_json(201, {"message": "Сообщение принято", "item": item})

        return self.send_json(404, {"error": "Маршрут не найден"})

    def log_message(self, format, *args):
        print("[Moon Light] " + (format % args))

if __name__ == "__main__":
    host, port = "127.0.0.1", 8000
    print(f"Moon Light запущен: http://{host}:{port}")
    print("Для остановки нажмите Ctrl+C")
    ThreadingHTTPServer((host, port), MoonLightHandler).serve_forever()
