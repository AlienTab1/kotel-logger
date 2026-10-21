"""Offline decoder: writes frame inventory, every slot transition and summary."""
import argparse, collections, csv, hashlib, json, statistics, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from jer_usb.capture import packets, transfers
from jer_usb.protocol import Stream, checksum, slots

def analyze(path, output, device=5):
    output.mkdir(parents=True,exist_ok=True)
    streams={}; spans={}; totals={}; frames=[]; previous={}; diffs=[]; counts=collections.Counter()
    all_packets=list(packets(path))
    for p in transfers(path,device):
        key=(p['bus'],p['device'],p['endpoint'])
        stream=streams.setdefault(key,Stream())
        begin=totals.get(key,0); totals[key]=begin+len(p['data'])
        spans.setdefault(key,[]).append((begin,totals[key],p['packet']))
        batch=stream.feed(p['data'])
        position=stream.consumed-sum(map(len,batch))
        for f in batch:
            start_packet=next(n for a,b,n in spans[key] if a<=position<b)
            position+=len(f)
            row=dict(packet_start=start_packet,packet_end=p['packet'],timestamp=p['timestamp'],
                     relative_s=round(p['timestamp']-all_packets[0]['timestamp'],6),
                     direction='in' if p['endpoint']&128 else 'out',type=f[4],length=len(f),
                     checksum_ok=checksum(f),hex=f.hex())
            counts[(row['direction'],f[4],len(f),row['checksum_ok'])]+=1
            if checksum(f) and row['direction']=='in' and f[4] in (13,20):
                row['slots']=slots(f)
                old=previous.get(f[4])
                if old:
                    for a,b in zip(old['slots'],row['slots']):
                        if (a['tag'],a['raw']) != (b['tag'],b['raw']):
                            diffs.append(dict(type=f[4],index=b['index'],offset=b['offset'],
                                packet_before=old['packet_end'],packet_after=p['packet'],
                                relative_s=row['relative_s'],before_tag=a['tag'],after_tag=b['tag'],
                                before_raw=a['raw'],after_raw=b['raw'],before=a['value'],after=b['value']))
                previous[f[4]]=row
            frames.append(row)
    with (output/'frames.jsonl').open('w',encoding='utf-8') as fh:
        for row in frames: fh.write(json.dumps(row)+'\n')
    with (output/'transitions.csv').open('w',newline='',encoding='utf-8') as fh:
        if diffs:
            w=csv.DictWriter(fh,fieldnames=list(diffs[0]));w.writeheader();w.writerows(diffs)
    summary=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),packets=len(all_packets),
        start=all_packets[0]['timestamp'],duration_s=all_packets[-1]['timestamp']-all_packets[0]['timestamp'],
        inventory=[dict(direction=k[0],type=k[1],length=k[2],checksum_ok=k[3],count=v) for k,v in sorted(counts.items())],
        discarded_bytes={str(k):v.discarded for k,v in streams.items()},
        pending_bytes={str(k):len(v.buffer) for k,v in streams.items()})
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    return frames,diffs

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('capture',type=Path);a.add_argument('--output',type=Path,default=Path('reports'));a.add_argument('--device',type=int,default=5)
    args=a.parse_args();analyze(args.capture,args.output,args.device)
