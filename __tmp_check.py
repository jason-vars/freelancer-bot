import ast, pathlib 
files=['bot/cli.py','bot/config.py','bot/webhook.py','bot/collector.py'] 
for f in files: ast.parse(pathlib.Path(f).read_text(encoding='utf-8')); print('ok',f) 
