import json,subprocess,os,sys,re
import tempfile,shutil
src=os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(src,'.claude','hooks','gate.py')): src=os.path.dirname(src)
root=tempfile.mkdtemp()
shutil.copytree(src,root,dirs_exist_ok=True,ignore=shutil.ignore_patterns(".git"))
os.chdir(root)
# phần đầu thử chế độ nháp, bất kể dự án đang đặt chế độ nào
_wf=open('.agents/workflow.yaml').read()
open('.agents/workflow.yaml','w').write(re.sub(r'mode: \w+','mode: draft',_wf))
def run(tool,inp,agent=None):
    d={"tool_name":tool,"tool_input":inp,"cwd":root}
    if agent: d["agent_type"]=agent
    o=subprocess.run(["python3",".claude/hooks/gate.py"],input=json.dumps(d),capture_output=True,text=True,env={**os.environ,"CLAUDE_PROJECT_DIR":root})
    return "DENY" if "deny" in o.stdout else "pass"
W=lambda p,a=None: run("Write",{"file_path":p,"content":"x"},a)
B=lambda c,a=None,d="Kiểm tra lệnh mẫu cho test": run("Bash",{"command":c,"description":d},a)
cases=[
 ("main ghi request",W(".agents/requests/REQ-0001.json"),"pass"),
 ("analyst ghi request",W(".agents/requests/REQ-0001.json","analyst"),"DENY"),
 ("analyst ghi artifact",W(".agents/artifacts/ART-0001.json","analyst"),"pass"),
 ("main sửa CLAUDE.md",W("CLAUDE.md"),"DENY"),
 ("executor sửa hook",W(".claude/hooks/gate.py","executor"),"DENY"),
 ("ghi ngoài repo",W("/etc/passwd","executor"),"DENY"),
 ("path traversal",W("drafts/REQ-0001/../../x","executor"),"DENY"),
 ("executor nháp chưa duyệt",W("drafts/REQ-0001/a.py","executor"),"DENY"),
]
os.makedirs(".agents/state",exist_ok=True)
json.dump({"agent":"executor","request_id":"REQ-0001","approved_by_decision":"DEC-0001"},open(".agents/state/active_task.json","w"))
json.dump({"status":"open","answer":{}},open(".agents/decisions/DEC-0001.json","w"))
cases.append(("executor, phiếu còn mở",W("drafts/REQ-0001/a.py","executor"),"DENY"))
json.dump({"status":"answered","answer":{"action":"reject"}},open(".agents/decisions/DEC-0001.json","w"))
cases.append(("executor, phiếu bị từ chối",W("drafts/REQ-0001/a.py","executor"),"DENY"))
json.dump({"status":"answered","answer":{"action":"choose"}},open(".agents/decisions/DEC-0001.json","w"))
cases.append(("executor, chưa có ledger",W("drafts/REQ-0001/a.py","executor"),"DENY"))
import subprocess as sp
def rec(q,agent=None):
    d={"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":q}]},"tool_response":{"answers":{q:"A"}},"cwd":root}
    if agent: d["agent_type"]=agent
    sp.run(["python3",".claude/hooks/record_answer.py"],input=json.dumps(d),text=True,env={**os.environ,"CLAUDE_PROJECT_DIR":root})
rec("[DEC-0001] Duyệt?","analyst")
cases.append(("ledger do subagent ghi không tính",W("drafts/REQ-0001/a.py","executor"),"DENY"))
rec("[DEC-0001] Duyệt ghi file a.py?")
cases.append(("main tự sửa ledger",W(".agents/ledger/answers.jsonl"),"DENY"))
cases+= [
 ("lệnh không mô tả",B("ls",None,""),"DENY"),
 ("executor đã duyệt",W("drafts/REQ-0001/a.py","executor"),"pass"),
 ("executor đã duyệt, sai REQ",W("drafts/REQ-0002/a.py","executor"),"DENY"),
 ("executor ghi src/",W("src/a.py","executor"),"DENY"),
 ("analyst ghi drafts",W("drafts/REQ-0001/a.py","analyst"),"DENY"),
 ("main sửa phiếu đã khoá",W(".agents/decisions/DEC-0001.json"),"DENY"),
 ("main ghi phiếu mới",W(".agents/decisions/DEC-0002.json"),"pass"),
 ("reviewer pytest",B("pytest -q 2>&1","reviewer"),"pass"),
 ("reviewer >/dev/null",B("npm test >/dev/null","reviewer"),"pass"),
 ("reviewer redirect",B("echo x > a.txt","reviewer"),"DENY"),
 ("main git push",B("git push origin main"),"DENY"),
 ("main curl",B("curl http://x"),"DENY"),
 ("analyst bash",B("ls","analyst"),"DENY"),
 ("main ls",B("ls -la"),"pass"),
 ("main cat .env",B("cat .env"),"DENY"),
]
# --- chế độ nhánh ---
wf=open(".agents/workflow.yaml").read().replace("mode: draft","mode: branch",1); open(".agents/workflow.yaml","w").write(wf)
os.makedirs(".git",exist_ok=True); open(".git/HEAD","w").write("ref: refs/heads/main\n")
cases.append(("nhánh: executor ghi src khi đang ở main",W("src/app.py","executor"),"DENY"))
open(".git/HEAD","w").write("ref: refs/heads/agent/REQ-0001\n")
cases+= [
 ("nhánh: executor ghi src đúng nhánh",W("src/app.py","executor"),"pass"),
 ("nhánh: executor ghi .github",W(".github/workflows/ci.yml","executor"),"DENY"),
 ("nhánh: executor ghi .agents/state",W(".agents/state/active_task.json","executor"),"DENY"),
 ("nhánh: analyst ghi src",W("src/app.py","analyst"),"DENY"),
 ("nhánh: main ghi src",W("src/app.py"),"DENY"),
]
open(".git/HEAD","w").write("ref: refs/heads/agent/REQ-0002\n")
cases.append(("nhánh: executor sai nhánh",W("src/app.py","executor"),"DENY"))
bad=[c for c in cases if c[1]!=c[2]]
for c in cases: print(("OK " if c[1]==c[2] else "FAIL"),c[0],c[1])
print(len(cases)-len(bad),"/",len(cases)); sys.exit(1 if bad else 0)
