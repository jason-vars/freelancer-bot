from bot.db import connect,init_db 
c=connect('bot_test.sqlite3') 
init_db(c) 
c.execute(\"INSERT INTO bot_state(key,value) VALUES ('k','v')\") 
c.commit() 
print('ok') 
