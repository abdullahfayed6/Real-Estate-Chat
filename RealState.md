اعملك Cheat Sheet صغيرة تحفظها عندك 

# 1. تشوف الـ API شغال ولا لا

```bash id="m9m6yy"
ps aux | grep "[u]vicorn"
```

لو رجع Process يبقى شغال.

---

# 2. تشوف آخر اللوج

```bash id="n7eh0s"
tail -50 /home2/mashimarketing/fastapi_app_real_state/cron_uvicorn.log
```

أو

```bash id="ewr6it"
tail -100 /home2/mashimarketing/fastapi_app_real_state/cron_uvicorn.log
```

---

# 3. تتابع اللوج Live

زي `docker logs -f`

```bash id="pm38es"
tail -f /home2/mashimarketing/fastapi_app_real_state/cron_uvicorn.log
```

للخروج:

```text id="m8tf9d"
Ctrl + C
```

---

# 4. تشوف الـ PID الحالي

```bash id="vfw9c6"
cat /home2/mashimarketing/fastapi_app_real_state/uvicorn.pid
```

---

# 5. توقف الـ API

```bash id="m4i3xa"
kill $(cat /home2/mashimarketing/fastapi_app_real_state/uvicorn.pid)
```

أو

```bash id="4p8n9v"
pkill -f uvicorn
```

---

# 6. تشغل الـ API يدوي

```bash id="htd8m7"
cd /home2/mashimarketing/fastapi_app_real_state

nohup /home2/mashimarketing/virtualenv/fastapi_app_real_state/3.11/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 >> cron_uvicorn.log 2>&1 &
```

---

# 7. تعمل Restart

```bash id="fbm5y7"
pkill -f uvicorn

/home2/mashimarketing/fastapi_app_real_state/start_uvicorn.sh
```

---

# 8. تشوف الـ Cron الحالي

```bash id="rqon58"
crontab -l
```

---

# 9. تعدل الـ Cron

```bash id="ltv4kr"
crontab -e
```

---

# 10. تشوف هل الموقع بيرد

```bash id="9r4q94"
curl https://fastapi.mashimarketing.com
```

أو

```bash id="gkft8w"
curl https://fastapi.mashimarketing.com/docs
```

---

# 11. تختبر Endpoint

مثال:

```bash id="4y9a4e"
curl https://fastapi.mashimarketing.com/openapi.json
```

---

# 12. تفضي اللوج لو كبر

```bash id="jjlwmv"
> /home2/mashimarketing/fastapi_app_real_state/cron_uvicorn.log
```

---

# 13. تشوف استهلاك الرام

```bash id="d3nm8r"
top
```

أو

```bash id="6xyl7h"
htop
```

لو موجود.

---

# 14. أهم ملفين هترجع لهم

```text id="1x0y2j"
/home2/mashimarketing/fastapi_app_real_state/start_uvicorn.sh

/home2/mashimarketing/fastapi_app_real_state/cron_uvicorn.log
```

دول لو حصل أي مشكلة مستقبلاً أول حاجتين تبص عليهم. 🚀
