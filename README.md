# Pagal Bhabhi Bot — Setup Guide

## Step 1: Apna Telegram ID pata karo
Telegram pe @userinfobot ko message karo — ID mil jaayega

## Step 2: GitHub repo banao
1. github.com pe naya repo banao: `pagalbhabhi-bot`
2. Teeno files upload karo: bot.py, requirements.txt, render.yaml

## Step 3: Render pe deploy karo
1. render.com → New → **Web Service**
2. GitHub repo connect karo
3. Environment Variables:
   - `BOT_TOKEN` = BotFather se mila token
   - `ADMIN_ID` = tumhara Telegram user ID
4. Deploy!

## Bot use karna:

### Normal post:
```
Video Title
https://terasharelink.com/s/xxxxx
```
+ Image attach karo

### Premium post:
```
Video Title  
https://terasharelink.com/s/xxxxx
#premium
```

## Notes:
- Bahut saari posts ek saath bhejo — queue mein jaayengi, ek ek karke upload hongi
- Pehli image use hogi
- Pehla terabox link use hoga
- Image nahi di toh placeholder use hoga
