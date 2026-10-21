"""Rebuild conservative map from the decoder report and screenshot ordering."""
import csv, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    frames=[json.loads(s) for s in (ROOT/'reports/frames.jsonl').read_text().splitlines()]
    names13=['DHW','Zone 1 outlet','Zone 2 outlet','Outdoor','Frost protection','Boiler outlet','Boiler return',
      'Thermal storage 1 top','Thermal storage 1 middle','Thermal storage 1 bottom','Thermal storage 2 top','Thermal storage 2 middle',
      'Thermal storage 2 bottom','Solar panel','Flue gas','Room 1','Room 2','Radiator head']
    mapping13={i:n for i,n in enumerate(names13)}
    mapping13.update({25:'Room thermostat 1',26:'Room thermostat 2',27:'Safety thermostat'})
    outputs=['Boiler fan','Boiler pump','Zone 1 pump','Zone 2 pump','DHW pump',
      'Boiler 1 switching','Boiler 2 switching','Valve actuator 1 open/close','Valve actuator 1 ON/OFF',
      'Valve actuator 2 open/close','Valve actuator 2 ON/OFF','Return valve actuator open/close','Return valve actuator ON/OFF','Solar']
    mapping13.update({35+i:n for i,n in enumerate(outputs)})
    mapping20=dict(enumerate(['Zone 1 water temperature target','Zone 2 water temperature target','Boiler 1B burner',
      'Boiler 1B ready','Boiler 2 ready','Flue thermostat','Boiler 1 running','Boiler 1 overheat','Frost protection',
      'Summer mode','Inhibit sensor','Zone 1 demand','Zone 2 demand','DHW demand','Maximum inhibit',
      'Zone 1 inhibit','Zone 2 inhibit','DHW inhibit','Boiler 2 inhibit','Solar consumer','Scheme',
      'Chimney sweep 1','Chimney sweep 2','Heat source disabled','Heat source 1 fault']))
    web={0:'s_t_tuv',1:'s_t_zo1_vy',2:'s_t_zo2_vy',3:'s_t_venku',5:'s_t_ko_vy',6:'s_t_ko_vr',
      7:'s_t_akuh',8:'s_t_akus',9:'s_t_akud',14:'s_t_spal'}
    rows=[]
    for t, names in ((13,mapping13),(20,mapping20)):
        fs=[f for f in frames if f['direction']=='in' and f['type']==t]
        for i in range(len(fs[0]['slots'])):
            samples=[f['slots'][i] for f in fs]
            tags=sorted(set(s['tag'] for s in samples))
            name=names.get(i,'UNKNOWN / reserved')
            reference=web.get(i,'') if t==13 else ''
            evidence='inferred_by_order' if i in names else 'unknown'
            if t==13 and i in range(1,10) or t==13 and i==14:evidence='confirmed_by_value'
            row=dict(block=f'0x{t:02x}',index=i,byte_offset=21+4*i,
              observed_tags=','.join(hex(v) for v in tags), decoding='0x0021: unsigned u16 LE / 10; 0x0022: only 0/1; other: missing',
              unit='°C' if t==13 and i<18 or t==20 and i in (0,1,10,11,12,13) else '',
              gui_name=name,web_signal=reference,web_metric='kotel_temperature_celsius' if reference else '',
              confidence='LIKELY' if i in names else 'UNKNOWN',evidence=evidence,
              source='terminal01..03.png; reports/frames.jsonl; transitions.csv',
              notes='Value match is not timestamp-synchronized physical identification; alias disabled')
            rows.append(row)
    with (ROOT/'field_map.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    # JSON is a YAML 1.2 subset; no runtime YAML dependency is required.
    (ROOT/'field_map.yaml').write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
    # Sanitize identity bytes while retaining real telemetry payloads and recalculating checksum.
    chosen=[next(f for f in frames if f['type']==13 and f.get('slots',[{},{}])[1].get('raw')==392),
            next(f for f in frames if f['type']==13 and any(s['tag']==290 for s in f.get('slots',[]))),
            next(f for f in frames if f['type']==20)]
    fixtures=[]
    for f in chosen:
        b=bytearray.fromhex(f['hex']);b[5:21]=bytes(16)
        b[-2:]=((~sum(b[:-2]))&65535).to_bytes(2,'big');fixtures.append(b.hex())
    (ROOT/'tests/fixtures.json').write_text(json.dumps(fixtures,indent=2))

if __name__=='__main__':main()
