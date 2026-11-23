# 🎓 MITS CAMPUS WEB

> Modern, real-time college attendance tracker, safe-bunk predictor & timetable dashboard integrated with MITS IMS / GEMS portal.

---

## 🚀 Key Features

- **⚡ Live GEMS Sync**: Real-time integration with student attendance & timetable (`http://mitsims.in`).
- **🔮 Safe-Bunk Calculator & What-If Simulator**: Predict how attending or bunking upcoming classes impacts your eligibility.
- **📱 PWA & Offline Support**: Installable directly to phone home screen as a standalone app with service worker caching.
- **🎨 Futuristic Cyber Circuit Branding**: High-tech vector typography wordmark with illuminated nodes and dark cosmic theme.

---

## 💻 Local Quick Start

Run the zero-dependency Python server:
```bash
python server.py
```
Then open:
- **Laptop / PC**: [http://localhost:5000](http://localhost:5000)
- **Mobile (Same Wi-Fi)**: Check the terminal for your LAN IP link (e.g. `http://192.168.x.x:5000`) or tap **Phone Access** in the top bar to scan the QR code!

---

## 🌐 Deploy to Vercel (Permanent 24/7 HTTPS)

1. Push this folder to a GitHub repository:
   ```bash
   git init
   git add .
   git commit -m "Initial MITS CAMPUS WEB release"
   git branch -M main
   git remote add origin https://github.com/RakeshReddy369/mits-campus-web.git
   git push -u origin main
   ```
2. Go to [vercel.com](https://vercel.com) and click **"Add New Project"**.
3. Import your `mits-campus-web` repository.
4. Click **"Deploy"**!
5. Your app is live with permanent HTTPS (e.g. `https://mits-campus-web.vercel.app`) with native 1-tap mobile installation!
