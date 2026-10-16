"""Read-only npm advisory audit using the existing closed project toolchain."""
import json
import os
import subprocess
import project


if __name__=='__main__':
    project.preflight('build',[])
    project.prepare()
    result=subprocess.run([str(project.NODE),str(project.NPM),'audit','--json','--package-lock-only','--ignore-scripts','--fetch-retries=0','--fetch-timeout=15000'],
        cwd=project.ROOT/'frontend',env=project.environment(dict(os.environ)),capture_output=True,text=True,encoding='utf-8',timeout=60)
    try:data=json.loads(result.stdout)
    except (ValueError,TypeError):raise SystemExit('Dependency advisory audit did not return JSON; acceptance remains unknown')
    if 'error' in data:raise SystemExit('Dependency advisory audit unavailable; acceptance remains unknown')
    print(json.dumps({'metadata':data.get('metadata',{}),'vulnerabilities':[
        {'name':name,'severity':value.get('severity'),'range':value.get('range'),'fix_available':value.get('fixAvailable'),
         'advisories':[item.get('url') for item in value.get('via',[]) if isinstance(item,dict)]}
        for name,value in data.get('vulnerabilities',{}).items()]},ensure_ascii=False))
