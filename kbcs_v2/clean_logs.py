import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('localhost', port=2222, username='p4', password='p4', timeout=5)
stdin, stdout, stderr = ssh.exec_command('rm -rf /home/p4/kbcs_v2/logs/* && rm -rf /home/p4/kbcs_v2/pcaps/*')
print("CLEANED")
ssh.close()
