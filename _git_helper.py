import subprocess, sys, os
os.chdir(r"C:\Users\PC-727\workspace\graphrag-demo")
cmds = sys.argv[1:]
result = subprocess.run(cmds, capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
if result.stderr: print("STDERR:", result.stderr)
sys.exit(result.returncode)
