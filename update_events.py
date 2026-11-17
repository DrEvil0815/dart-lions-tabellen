#!/usr/bin/env python3
"""Extract only publicly visible Dart Lions homepage events; no private SportMember calendar."""
import json,re,datetime,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pathlib import Path
BASE='https://www.dart-lions.de'
URL=BASE+'/klub/dart-lions-tsv-09-graefenberg/sider/dart-lions-tsv-09-grafenberg-lorem-ipsum-dolor-sit-amet-consectetur-adipiscing-elit-integer-sed-semper-sapien-a-faucibus-mauris-mauris-orci-mi-auctor-nec-urna-ac-venenatis-commodo-velit-sed-ultricies-justo-ultrices-suscipit-pellentesque'
OUT=Path(__file__).with_name('arena-events.json')
MONTHS={'januar':1,'februar':2,'märz':3,'maerz':3,'april':4,'mai':5,'juni':6,'juli':7,'august':8,'september':9,'oktober':10,'november':11,'dezember':12}
KEYWORDS=('competition','dart','liga','turnier','open','weihnacht','benefiz','meisterschaft')
def clean(x):return re.sub(r'\s+',' ',x or '').strip()
def poster_from_article(soup,href):
 try:
  page=BeautifulSoup(requests.get(href,timeout=20,headers={'User-Agent':'DartLionsArenaBot/1.0'}).text,'html.parser')
  candidates=[]
  for im in page.select('main img,article img,.news img,img'):
   src=im.get('data-src') or im.get('src') or ''
   if 'holdsport.dk/media/' in src:
    candidates.append(urljoin(href,src))
  # Prefer meaningful event poster; never use club header or sponsor logo.
  for src in candidates:
   if any(k in src.lower() for k in ('open','turnier','weihnacht','plakat')):return src
  return candidates[0] if len(candidates)==1 else None
 except Exception:return None
def run():
 response=requests.get(URL,timeout=30,headers={'User-Agent':'Mozilla/5.0 (compatible; DartLionsArena/1.0)'})
 response.raise_for_status();soup=BeautifulSoup(response.text,'html.parser')
 today=datetime.date.today();events=[];seen=set()
 # News posts for major tournaments; only dated event posts, not generic news.
 for a in soup.find_all('a',href=True):
  txt=clean(a.get_text(' ',strip=True));href=urljoin(URL,a['href'])
  if not any(k in txt.lower() for k in ('darts open','weihnachtsspezial','turnier')):continue
  m=re.search(r'(\d{1,2})\.\s*(?:und|&|[-–])\s*(\d{1,2})\.\s*(januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember)\s*(20\d\d)',txt,re.I)
  if m:day,month,year=int(m[1]),MONTHS[m[3].lower()],int(m[4]);end=int(m[2])
  else:
   m=re.search(r'(\d{1,2})\.\s*(januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember)\s*(20\d\d)',txt,re.I)
   if not m:continue
   day,month,year=int(m[1]),MONTHS[m[2].lower()],int(m[3]);end=day
  try:date=datetime.date(year,month,day);enddate=datetime.date(year,month,end)
  except ValueError:continue
  if enddate<today or href in seen:continue
  seen.add(href)
  title=txt.split(' Am ')[0].split(' Darts spielen ')[0][:110]
  events.append({'title':title,'date':date.isoformat(),'end_date':enddate.isoformat(),'time':'','type':'poster','poster':poster_from_article(soup,href),'url':href})
 # Public homepage schedule. Extract a date from the month header and day badge.
 for header in soup.find_all(string=re.compile(r'^\s*(?:'+'|'.join(MONTHS)+r')\s*$',re.I)):
  month=MONTHS[clean(str(header)).lower()]
  # Search a bounded block of text following month header until next month.
  root=header.parent
  # Homepage markup varies; walk sibling containers near heading.
  parent=root.parent
  for _ in range(3):
   if parent and len(parent.get_text(' ',strip=True))<20000:parent=parent.parent
  if not parent:continue
  text=parent.get_text('\n',strip=True)
  start=text.lower().find(clean(str(header)).lower())
  text=text[start+len(str(header)):]
  stop=re.search(r'\n(?:'+'|'.join(MONTHS)+r')\n',text,re.I)
  if stop:text=text[:stop.start()]
  lines=[clean(x) for x in text.splitlines() if clean(x)]
  for i,line in enumerate(lines):
   if line not in ('Mo','Di','Mi','Do','Fr','Sa','So'):continue
   if i+2>=len(lines) or not re.fullmatch(r'\d{1,2}',lines[i+1]):continue
   day=int(lines[i+1]);title=lines[i+2]
   if not any(k in title.lower() for k in KEYWORDS):continue
   year=today.year+(month<today.month-1)
   try:date=datetime.date(year,month,day)
   except ValueError:continue
   if date<today:continue
   tm=next((x for x in lines[i+3:i+7] if re.match(r'^\d\d?:\d\d',x)),'')
   key=(date.isoformat(),title)
   if key in seen:continue
   seen.add(key)
   events.append({'title':title,'date':date.isoformat(),'time':tm.split(' - ')[0],'type':'termin','poster':None,'url':URL})
 # Fail safe: do not overwrite last known good data with an empty or malformed scrape.
 if not events:raise RuntimeError('No public upcoming events parsed; keeping existing JSON unchanged')
 events.sort(key=lambda x:x['date'])
 OUT.write_text(json.dumps({'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':URL,'events':events},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 print('Updated',len(events),'public events')
if __name__=='__main__':run()
