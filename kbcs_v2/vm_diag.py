import paramiko
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('localhost', port=2222, username='p4', password='p4', timeout=10)

# Run comprehensive diagnostic on VM
stdin, stdout, stderr = ssh.exec_command(
    'ps aux | grep simple_switch | grep -v grep | head -3 && '
    'echo "---SWITCH_STATUS---" && '
    'ss -tlnp 2>/dev/null | grep 9090 && '
    'echo "---BATCH_READ---" && '
    'echo "register_read MyIngress.reg_total_pkts 1" | simple_switch_CLI --thrift-port 9090 2>/dev/null | grep "=" && '
    'echo "register_read MyIngress.reg_forwarded_bytes 1" | simple_switch_CLI --thrift-port 9090 2>/dev/null | grep "=" && '
    'echo "---RL_LOG---" && '
    'tail -5 /tmp/kbcs_rl_s1.log 2>/dev/null || echo NO_RL_LOG'
)

time.sleep(15)
out = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')
print("STDOUT:", out[:3000])
if err:
    print("STDERR:", err[:500])
ssh.close()
