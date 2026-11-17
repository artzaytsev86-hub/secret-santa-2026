import os, secrets, hashlib, random
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import wraps
from flask import Flask, request, redirect, render_template_string, abort, session, url_for

app=Flask(__name__)
app.secret_key=os.environ.get('FLASK_SECRET_KEY',secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True)
DB=os.environ.get('DATABASE_URL','')
ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD','')
TZ=ZoneInfo(os.environ.get('SANTA_TIMEZONE','Asia/Yekaterinburg'))
MAX_PARTICIPANTS=10
BUDGET=2000

def digest(t):return hashlib.sha256(t.encode()).hexdigest()
class Row(dict):
 def __getitem__(self, key):
  if isinstance(key,int): return list(self.values())[key]
  return super().__getitem__(key)
class Cursor:
 def __init__(self, cursor):self.cursor=cursor
 def fetchone(self):
  r=self.cursor.fetchone();return Row(r) if r is not None else None
 def fetchall(self):return [Row(r) for r in self.cursor.fetchall()]
class Database:
 def __init__(self):self.c=psycopg.connect(DB,row_factory=dict_row)
 def __enter__(self):self.c.__enter__();return self
 def __exit__(self,*a):return self.c.__exit__(*a)
 def execute(self,sql,params=()):
  sql=sql.replace('?', '%s')
  if sql=='BEGIN IMMEDIATE':
   self.c.execute('SELECT pg_advisory_xact_lock(20261130)')
   return Cursor(self.c.execute('SELECT 1'))
  return Cursor(self.c.execute(sql,params))
 def commit(self):self.c.commit()
def db():return Database()
def init():
 with db() as c:
  c.execute("""CREATE TABLE IF NOT EXISTS participants (
   id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
   token_hash TEXT UNIQUE NOT NULL, name TEXT DEFAULT '',
   interests TEXT DEFAULT '', likes TEXT DEFAULT '', dislikes TEXT DEFAULT '',
   notes TEXT DEFAULT '', registered INTEGER DEFAULT 0,
   recipient_id BIGINT, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""")
  c.execute('CREATE UNIQUE INDEX IF NOT EXISTS recipient_unique ON participants(recipient_id) WHERE recipient_id IS NOT NULL')
  c.execute('CREATE TABLE IF NOT EXISTS game_state (id INTEGER PRIMARY KEY, drawn_at TIMESTAMPTZ)')
  c.execute('INSERT INTO game_state(id,drawn_at) VALUES (1,NULL) ON CONFLICT(id) DO NOTHING')
init()
CSS='''<style>:root{font-family:Inter,system-ui,sans-serif;color:#f7f0df;background:#101d19}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#30463b,#101d19 65%);min-height:100vh}.wrap{max-width:850px;margin:auto;padding:32px 18px}header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:38px}.logo{font-family:Georgia,serif;font-size:27px;color:#ead6a0}.tag{color:#b6c2b7;font-size:13px}.hero{padding:30px 0 18px}h1,h2{font-family:Georgia,serif;font-weight:400}h1{font-size:clamp(38px,7vw,65px);line-height:1.1;margin:0 0 14px}h2{font-size:28px}p{line-height:1.6;color:#d2d8d1}.gold{color:#e5c78a}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:13px}.card{background:#1c2b25;border:1px solid #3a4d3f;border-radius:18px;padding:23px;margin:15px 0;box-shadow:0 8px 30px #0002}.stat{font-size:30px;color:#e5c78a;font-family:Georgia,serif}.muted{color:#b6c2b7;font-size:14px}label{display:block;margin:14px 0 6px;color:#e9e1ce}input,textarea{width:100%;padding:12px;border-radius:9px;border:1px solid #637469;background:#101d19;color:white;font:inherit}textarea{min-height:76px}button,.btn{background:#d7b879;color:#14221b;border:0;border-radius:9px;padding:12px 20px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block;margin-top:14px}button:hover,.btn:hover{background:#f0d79e}.pill{display:inline-block;border:1px solid #718476;border-radius:40px;padding:7px 12px;margin:5px;color:#f4e8c9}.error{color:#ffb8b8}.link{word-break:break-all;padding:10px;background:#101d19;border-radius:7px}.foot{font-size:12px;color:#a7b3a8;margin-top:36px}ul{color:#d2d8d1;line-height:1.9}a{color:#e5c78a}</style>'''
BASE='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Тайный Санта</title>'''+CSS+'''</head><body><main class="wrap"><header><div class="logo">✦ Тайный Санта</div><div class="tag">Подарки с интригой · 2026</div></header>{{ body|safe }}<div class="foot">Не пересылайте личную ссылку: она даёт доступ к вашей анкете и результату. Время мероприятия: {{ timezone }}.</div></main></body></html>'''
def page(body,**kw):return render_template_string(BASE,body=render_template_string(body,**kw),timezone=str(TZ))
def now():return datetime.now(TZ)
def roster(c):return c.execute('SELECT name FROM participants WHERE registered=1 ORDER BY name').fetchall()
def drawn(c):
 return c.execute('SELECT drawn_at FROM game_state WHERE id=1').fetchone()['drawn_at'] is not None

def draw(c):
 # Only an authenticated organizer calls this. Serialize all game changes.
 c.execute('SELECT pg_advisory_xact_lock(20261129)')
 if drawn(c):return False,'Жеребьёвка уже проведена.'
 rows=c.execute('SELECT id,registered FROM participants ORDER BY id').fetchall()
 if len(rows)<2:return False,'Нужно минимум два приглашённых участника.'
 if any(not r['registered'] for r in rows):return False,'Не все приглашённые заполнили анкеты.'
 ids=[r['id'] for r in rows]
 recipients=ids[:]
 for i in range(len(recipients)-1,0,-1):
  j=secrets.randbelow(i)
  recipients[i],recipients[j]=recipients[j],recipients[i]
 for giver,receiver in zip(ids,recipients):
  c.execute('UPDATE participants SET recipient_id=? WHERE id=?',(receiver,giver))
 c.execute('UPDATE game_state SET drawn_at=CURRENT_TIMESTAMP WHERE id=1')
 c.commit()
 return True,'Жеребьёвка проведена. Участники могут открыть свои результаты по личным ссылкам.'

RULES='''<div class="card"><h2>Краткие правила</h2><ul><li>Участие только по личной секретной ссылке.</li><li>Заполните анкету о своих интересах и пожеланиях до жеребьёвки.</li><li>Бюджет подарка — <strong>до 2 000 ₽</strong>.</li><li>Жеребьёвку запускает организатор после заполнения всех приглашений. После запуска имя получателя и его анкета станут доступны по личной ссылке.</li><li>Не рассказывайте, кому дарите подарок, до обмена подарками.</li><li>Каждый дарит одному человеку и получает подарок от другого.</li></ul></div>'''

@app.get('/')
def home():
 with db() as c:
  names=roster(c);slots=c.execute('SELECT COUNT(*) FROM participants').fetchone()[0];revealed=drawn(c)
 return page('''<section class="hero"><div class="gold">✧ Секретный обмен подарками</div><h1>Маленький секрет.<br><span class="gold">Большая радость.</span></h1><p>Знакомимся, делимся увлечениями и готовим подарки друг другу. Пары останутся тайной до самого обмена.</p></section><div class="grid"><div class="card"><div class="stat">{{ n }} / 10</div><div class="muted">участников в игре</div></div><div class="card"><div class="stat">2 000 ₽</div><div class="muted">максимальный бюджет подарка</div></div><div class="card"><div class="stat">{{ "Проведена" if revealed else "Ожидание" }}</div><div class="muted">статус жеребьёвки</div></div></div><div class="card"><h2>Кто уже в игре</h2>{% if names %}{% for p in names %}<span class="pill">✦ {{ p['name'] }}</span>{% endfor %}{% else %}<p>Пока никто не заполнил анкету.</p>{% endif %}<p class="muted">Приглашено: {{ slots }} из 10. Имена видны всем, анкеты — только назначенным дарителям после жеребьёвки.</p></div>'''+RULES,names=names,n=len(names),slots=slots,revealed=revealed)

@app.route('/p/<token>',methods=['GET','POST'])
def participant(token):
 with db() as c:
  p=c.execute('SELECT * FROM participants WHERE token_hash=?',(digest(token),)).fetchone()
  if not p:abort(404)
  if request.method=='POST':
   c.execute('SELECT pg_advisory_xact_lock(20261129)')
   if drawn(c):abort(403,'Регистрация закрыта после жеребьёвки')
   vals=[request.form.get(k,'').strip() for k in ('name','interests','likes','dislikes','notes')]
   if not vals[0] or len(vals[0])>80 or any(len(x)>800 for x in vals[1:]):abort(400,'Проверьте длину полей')
   c.execute('UPDATE participants SET name=?,interests=?,likes=?,dislikes=?,notes=?,registered=1 WHERE id=?',(*vals,p['id']))
   c.commit();return redirect(url_for('participant',token=token))
  revealed=drawn(c)
  recipient=c.execute('SELECT * FROM participants WHERE id=?',(p['recipient_id'],)).fetchone() if revealed and p['registered'] and p['recipient_id'] else None
  names=roster(c)
 return page('''<h1>Ваш праздничный кабинет</h1><p>Личная ссылка — только для вас. <a href="/">Общая страница и правила</a></p><div class="grid"><div class="card"><div class="stat">{{ names|length }}/10</div><div class="muted">участников</div></div><div class="card"><div class="stat">2 000 ₽</div><div class="muted">бюджет подарка</div></div></div>{% if recipient %}<div class="card"><div class="gold">🎁 Ваш получатель</div><h1>{{ recipient['name'] }}</h1><p><strong>Увлечения:</strong> {{ recipient['interests'] or 'Не указано' }}</p><p><strong>Что нравится:</strong> {{ recipient['likes'] or 'Не указано' }}</p><p><strong>Что не дарить:</strong> {{ recipient['dislikes'] or 'Не указано' }}</p><p><strong>Дополнительно:</strong> {{ recipient['notes'] or 'Не указано' }}</p><p class="muted">Сохраните интригу: не раскрывайте имя получателя.</p></div>{% elif revealed %}<div class="card"><h2>Жеребьёвка пока невозможна</h2><p>Для игры нужно минимум два человека с заполненными анкетами.</p></div>{% else %}<div class="card"><h2>🔒 Ожидаем жеребьёвку</h2><p>Организатор запустит жеребьёвку после заполнения анкет. Когда она состоится, обновите эту страницу, чтобы увидеть получателя.</p></div>{% endif %}{% if not revealed %}<div class="card"><h2>Ваша анкета</h2><form method="post"><label>Ваше имя *</label><input name="name" maxlength="80" required value="{{ p['name'] }}" placeholder="Как к вам обращаться"><label>Ваши увлечения</label><textarea name="interests" maxlength="800" placeholder="Книги, игры, музыка, спорт...">{{ p['interests'] }}</textarea><label>Что вам нравится, тематика подарков</label><textarea name="likes" maxlength="800" placeholder="Любимые цвета, фильмы, сладости, хобби...">{{ p['likes'] }}</textarea><label>Что лучше не дарить</label><textarea name="dislikes" maxlength="800" placeholder="Аллергии, нежелательные вещи...">{{ p['dislikes'] }}</textarea><label>Дополнительные пожелания</label><textarea name="notes" maxlength="800" placeholder="Любая подсказка вашему Санте">{{ p['notes'] }}</textarea><button>Сохранить анкету</button></form></div>{% endif %}<div class="card"><h2>Кто участвует</h2>{% for person in names %}<span class="pill">{{ person['name'] }}</span>{% endfor %}</div>''',p=p,names=names,recipient=recipient,revealed=revealed)

def admin_ok():return bool(ADMIN_PASSWORD) and session.get('admin')==digest(ADMIN_PASSWORD)
@app.route('/admin',methods=['GET','POST'])
def admin():
 if not ADMIN_PASSWORD:return page('<h2>Администратор не настроен</h2><p>Задайте ADMIN_PASSWORD на сервере.</p>'),503
 if request.method=='POST' and request.form.get('action')=='login':
  if secrets.compare_digest(request.form.get('password',''),ADMIN_PASSWORD):session['admin']=digest(ADMIN_PASSWORD);return redirect('/admin')
  return page('<h2>Неверный пароль</h2><a href="/admin">Попробовать снова</a>'),403
 if not admin_ok():return page('''<h1>Вход организатора</h1><div class="card"><form method="post"><input type="hidden" name="action" value="login"><label>Пароль</label><input type="password" name="password" required><button>Войти</button></form></div>''')
 created=None
 test_result=None
 with db() as c:
  if request.method=='POST' and request.form.get('action')=='invite':
   c.execute('SELECT pg_advisory_xact_lock(20261129)')
   if drawn(c):abort(403,'Жеребьёвка уже проведена')
   if c.execute('SELECT COUNT(*) FROM participants').fetchone()[0]>=MAX_PARTICIPANTS:abort(400,'Все 10 мест заняты')
   token=secrets.token_urlsafe(32)
   c.execute('INSERT INTO participants(token_hash) VALUES (?)',(digest(token),));c.commit()
   created=request.url_root.rstrip('/')+url_for('participant',token=token)
  if request.method=='POST' and request.form.get('action')=='delete':
   if drawn(c):abort(403,'После жеребьёвки удаление закрыто')
   try: player_id=int(request.form.get('player_id',''))
   except ValueError:abort(400)
   c.execute('SELECT pg_advisory_xact_lock(20261129)')
   player=c.execute('SELECT id FROM participants WHERE id=?',(player_id,)).fetchone()
   if not player:abort(404)
   c.execute('DELETE FROM participants WHERE id=?',(player_id,))
   c.commit()
   return redirect('/admin')
  if request.method=='POST' and request.form.get('action')=='rotate':
   if drawn(c):abort(403,'После жеребьёвки ссылки менять нельзя')
   try: player_id=int(request.form.get('player_id',''))
   except ValueError:abort(400)
   c.execute('SELECT pg_advisory_xact_lock(20261129)')
   player=c.execute('SELECT id FROM participants WHERE id=?',(player_id,)).fetchone()
   if not player:abort(404)
   token=secrets.token_urlsafe(32)
   c.execute('UPDATE participants SET token_hash=? WHERE id=?',(digest(token),player_id))
   c.commit()
   created=request.url_root.rstrip('/')+url_for('participant',token=token)
  if request.method=='POST' and request.form.get('action')=='test_draw':
   if drawn(c):abort(403,'Тестирование закрыто после жеребьёвки')
   rows=c.execute('SELECT id FROM participants WHERE registered=1 ORDER BY id').fetchall()
   if len(rows)<2:
    test_result='Для проверки нужны минимум две заполненные анкеты. Сейчас: '+str(len(rows))+'.'
   else:
    ids=[row['id'] for row in rows]
    receivers=ids[:]
    for i in range(len(receivers)-1,0,-1):
     j=secrets.randbelow(i)
     receivers[i],receivers[j]=receivers[j],receivers[i]
    valid=(len(set(receivers))==len(ids) and all(a!=b for a,b in zip(ids,receivers)))
    test_result=('Проверка успешна: ' if valid else 'Ошибка проверки: ')+str(len(ids))+' участников; каждому назначен уникальный получатель, совпадений с собой нет. Настоящие пары не созданы и не сохранены.' if valid else 'Проверка не пройдена. Настоящие пары не изменены.'
  if request.method=='POST' and request.form.get('action')=='start_draw':
   _,test_result=draw(c)
  revealed=drawn(c)
  players=c.execute('SELECT id,name,registered FROM participants ORDER BY id').fetchall()
 return page('''<h1>Кабинет организатора</h1><p>Создайте персональную ссылку и отправьте её одному участнику. Ссылка показывается только один раз — сохраните её перед закрытием страницы.</p>{% if test_result %}<div class="card"><h2>Результат действия</h2><p>{{ test_result }}</p></div>{% endif %}{% if created %}<div class="card"><h2>Новая секретная ссылка</h2><div class="link">{{ created }}</div><p class="muted">Скопируйте её сейчас: в базе хранится только хеш токена.</p></div>{% endif %}<div class="grid"><div class="card"><div class="stat">{{ registered }}/10</div><div class="muted">заполнили анкету</div></div><div class="card"><div class="stat">{{ players|length }}/10</div><div class="muted">создано приглашений</div></div></div><div class="card"><form method="post"><input type="hidden" name="action" value="invite"><button {% if players|length>=10 or revealed %}disabled{% endif %}>+ Создать личную ссылку</button></form><div class="card"><h2>Проверка жеребьёвки</h2><p class="muted">Проверка использует заполненные анкеты, но не сохраняет пары и не показывает получателей.</p><form method="post"><input type="hidden" name="action" value="test_draw"><button {% if revealed %}disabled{% endif %}>Проверить жеребьёвку</button></form></div><div class="card"><h2>Настоящая жеребьёвка</h2>{% if revealed %}<p>Жеребьёвка проведена. Участники могут посмотреть результаты по личным ссылкам.</p>{% else %}<p class="muted">Запуск возможен, если зарегистрированы минимум два участника и все созданные приглашения заполнены. После запуска анкеты и список участников будут заблокированы.</p><form method="post" onsubmit="return confirm('ВНИМАНИЕ! Запустить настоящую жеребьёвку? Результаты сразу станут доступны участникам. Отменить действие нельзя.')"><input type="hidden" name="action" value="start_draw"><button type="submit" style="background:#b96d48;color:white" {% if registered<2 or registered!=players|length %}disabled{% endif %}>Запустить настоящую жеребьёвку</button></form>{% endif %}</div><h2>Список участников</h2>{% for p in players %}<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><p>№{{ p['id'] }} · {{ p['name'] if p['registered'] else 'Ожидает анкету' }} · {{ '✓' if p['registered'] else '○' }}</p>{% if not revealed %}<form method="post" onsubmit="return confirm('Удалить анкету и аннулировать личную ссылку?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="player_id" value="{{ p['id'] }}"><button type="submit" style="background:#a85b55;color:white">Удалить</button></form><form method="post" onsubmit="return confirm('Старая ссылка сразу перестанет работать. Создать новую?')"><input type="hidden" name="action" value="rotate"><input type="hidden" name="player_id" value="{{ p['id'] }}"><button type="submit">Заменить ссылку</button></form>{% endif %}</div>{% endfor %}</div><p class="muted">Организатор не видит пары через интерфейс. Для сильной гарантии от администратора сервера нужна отдельная криптографическая архитектура.</p>''',created=created,test_result=test_result,players=players,registered=sum(x['registered'] for x in players),revealed=now()>=REVEAL)

if __name__=='__main__':
 if not ADMIN_PASSWORD:raise SystemExit('Укажите ADMIN_PASSWORD перед запуском')
 app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5000')),debug=False)
