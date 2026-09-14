"""单条HTTP实证使用的Protobuf编解码；不会自行发起网络请求。"""
from dataclasses import dataclass,field
from urllib.parse import urlsplit
from bdhub.imbase.http_readonly_probe import wire_fields, READ_HOST

SEND_PATH="/v1/message/send"
READBACK_PATH="/v1/message/get_by_id"
HISTORY_PATH="/v1/message/get_by_conversation"
CONVERSATION_PATH="/v2/conversation/get_info"


@dataclass(frozen=True, repr=False)
class HttpConversation:
    full_cid: bytes
    short_cid: int
    conversation_type: int
    ticket: bytes
    oec: str


def build_conversation_read(packet, cid, *, sequence):
    if not str(cid).isdigit() or int(cid) <= 0:
        raise ValueError("http_conversation_id_invalid")
    # 原生pullConversationById同样将短CID用于两个字段。
    body=vb(1,str(cid))+vi(2,int(cid))+vi(3,packet.conversation_type)
    env=wire_fields(packet.data)
    env[1]=[608];env[2]=[sequence];env[8]=[vb(608,body)]
    return encode_fields(env)


def decode_conversation(data, *, sequence, cid, oec):
    outer=decode_envelope(data,cmd=608,sequence=sequence)
    body=wire_fields(one(outer,608,b""))
    info=wire_fields(one(body,1,b""))
    core=wire_fields(one(info,50,b""))
    ext={one(wire_fields(v),1):one(wire_fields(v),2) for v in core.get(11,[])}
    if (one(info,2)!=int(cid) or not one(info,1) or not one(info,4)
            or ext.get(b"creator_oec_id")!=str(oec).encode()
            or not isinstance(one(info,3),int)):
        raise ValueError("http_conversation_identity_or_ticket_mismatch")
    return HttpConversation(one(info,1),one(info,2),one(info,3),one(info,4),str(oec))


def build_fresh_send(packet, conversation, *, text, sequence, client_id, ext=None):
    from uuid import UUID
    if str(UUID(client_id))!=client_id or sequence==packet.sequence or not text:
        raise ValueError("http_fresh_send_invalid")
    body=(vb(1,conversation.full_cid)+vi(2,conversation.conversation_type)
          +vi(3,conversation.short_cid)+vb(4,text)+vi(6,1000)
          +vb(7,conversation.ticket)+vb(8,client_id))
    for key,value in (packet.message_ext if ext is None else ext).items():
        body+=vb(5,vb(1,key)+vb(2,value))
    env=wire_fields(packet.data)
    env[1]=[100];env[2]=[sequence];env[8]=[vb(100,body)]
    return validate_packet(packet.url,packet.headers,encode_fields(env),cid=str(conversation.short_cid),text=text)

def varint(value):
    if type(value) is not int or value<0: raise ValueError("invalid_varint")
    out=bytearray()
    while value>127:out.append((value&127)|128);value>>=7
    out.append(value);return bytes(out)

def vi(field,value):return varint(field<<3)+varint(value)
def vb(field,value):
    if isinstance(value,str):value=value.encode()
    return varint((field<<3)|2)+varint(len(value))+value

def one(fields,key,default=None):
    values=fields.get(key,[])
    if not values:return default
    if len(values)!=1:raise ValueError("duplicate_proto_field")
    return values[0]

def encode_fields(fields):
    # 原生Request Envelope只有varint/string/bytes/message，无fixed32/64字段。
    return b"".join(vi(k,v) if isinstance(v,int) else vb(k,v) for k,values in fields.items() for v in values)

@dataclass(frozen=True,repr=False)
class Packet:
    url:str
    headers:dict
    data:bytes
    sequence:int
    full_cid:bytes
    short_cid:int
    conversation_type:int
    client_id:bytes
    text:bytes
    sender_id:int
    message_ext:dict=field(default_factory=dict)

def validate_packet(url,headers,data,*,cid,text):
    u=urlsplit(url)
    if u.scheme!="https" or u.hostname!=READ_HOST or u.path!=SEND_PATH or u.port or u.username or u.password:
        raise ValueError("http_send_endpoint_mismatch")
    env=wire_fields(data)
    if one(env,1)!=100 or not isinstance(one(env,2),int) or not one(env,4):
        raise ValueError("http_send_envelope_invalid")
    outer=wire_fields(one(env,8,b""))
    if set(outer)!={100}:raise ValueError("http_send_body_mismatch")
    body=wire_fields(one(outer,100))
    content=text.encode()
    if one(body,3)!=int(cid) or one(body,4)!=content or one(body,6)!=1000 or not one(body,7) or not one(body,8):
        raise ValueError("http_send_target_or_content_mismatch")
    if not one(body,1) or not isinstance(one(body,2),int):raise ValueError("http_send_conversation_missing")
    ext={one(wire_fields(v),1).decode():one(wire_fields(v),2).decode() for v in body.get(5,[])}
    return Packet(url,headers,data,one(env,2),one(body,1),one(body,3),one(body,2),one(body,8),content,int(one(env,9)),ext)

def decode_envelope(data,*,cmd,sequence):
    fields=wire_fields(data)
    if one(fields,1)!=cmd or one(fields,2)!=sequence:raise ValueError("http_receipt_correlation")
    if one(fields,3,0)!=0:raise ValueError("http_receipt_outer_rejected")
    return wire_fields(one(fields,6,b""))

class HttpSendRejected(ValueError):
    """有相关性且明确的原生SendMessageStatus拒绝；不是网络结果未知。"""
    def __init__(self,status,check_code):
        self.status=status
        self.check_code=check_code
        super().__init__(f'http_send_rejected:status={status}:check_code={check_code}')


def decode_send_receipt(data,packet):
    outer=decode_envelope(data,cmd=100,sequence=packet.sequence)
    body=wire_fields(one(outer,100,b""))
    client_id=one(body,4,b"")
    if client_id and client_id!=packet.client_id:
        raise ValueError('http_receipt_message_mismatch')
    status=one(body,3,0);check_code=one(body,5,0)
    if not isinstance(status,int) or not isinstance(check_code,int):
        raise ValueError('http_receipt_business_invalid')
    if status in (1,2,3,4,5):raise HttpSendRejected(status,check_code)
    if status!=0:raise ValueError('http_receipt_business_unknown_status')
    # 原生SDK只以status==0判正向候选；check_code仍保留诊断，最终还须HTTP回查。
    server_id=one(body,1,0)
    if not isinstance(server_id,int) or server_id<=0 or (client_id and client_id!=packet.client_id):
        raise ValueError("http_receipt_message_mismatch")
    return server_id


def send_receipt_diagnostics(data,packet):
    """只保留相关性与业务数值，不记录原始响应/凭据；拒绝时也必须可审计。"""
    env=wire_fields(data)
    result={'cmd':one(env,1),'sequence_match':one(env,2)==packet.sequence,'outer_status':one(env,3,0)}
    if result['cmd']!=100 or not result['sequence_match']:return result
    if result['outer_status']!=0:return result
    body=wire_fields(one(wire_fields(one(env,6,b'')),100,b''))
    result.update(status=one(body,3,0),check_code=one(body,5,0),
                  platform_id_present=isinstance(one(body,1),int) and one(body,1)>0,
                  client_id_match=one(body,4,b'') in (b'',packet.client_id))
    if result['platform_id_present']:result['candidate_platform_message_id']=str(one(body,1))
    return result

def build_readback(packet,server_id,*,sequence):
    body=vb(1,packet.full_cid)+vi(2,packet.conversation_type)+vi(3,packet.short_cid)+vi(4,server_id)
    env=wire_fields(packet.data)
    env[1]=[211];env[2]=[sequence];env[8]=[vb(211,body)]
    return encode_fields(env)

def verify_readback(data,packet,server_id,*,sequence):
    outer=decode_envelope(data,cmd=211,sequence=sequence)
    response=wire_fields(one(outer,211,b""))
    info=wire_fields(one(response,1,b""))
    if one(info,1,0)!=0:raise ValueError("http_readback_message_unavailable")
    msg=wire_fields(one(info,2,b""))
    ext={}
    for entry in msg.get(9,[]):
        item=wire_fields(entry);ext[one(item,1)]=one(item,2)
    if (one(msg,1)!=packet.full_cid or one(msg,5)!=packet.short_cid or one(msg,3)!=server_id or one(msg,7)!=packet.sender_id or one(msg,6)!=1000
            or one(msg,8)!=packet.text or ext.get(b"s:client_message_id")!=packet.client_id):
        raise ValueError("http_readback_message_mismatch")
    return True


def build_history_readback(packet, *, sequence, anchor=0):
    body=vb(1,packet.full_cid)+vi(2,packet.conversation_type)+vi(3,packet.short_cid)+vi(4,1)+vi(5,anchor)+vi(6,20)
    env=wire_fields(packet.data);env[1]=[301];env[2]=[sequence];env[8]=[vb(301,body)]
    return encode_fields(env)


def history_checks(data,packet,server_id,*,sequence):
    outer=wire_fields(data)
    result={"cmd":one(outer,1),"sequence_match":one(outer,2)==sequence,"outer_status":one(outer,3,0)}
    if result!={"cmd":301,"sequence_match":True,"outer_status":0}:return result
    body=wire_fields(one(wire_fields(one(outer,6,b'')),301,b''))
    result['messages_returned']=len(body.get(1,[]))
    for value in body.get(1,[]):
        msg=wire_fields(value)
        if one(msg,3)!=server_id:continue
        ext={one(wire_fields(v),1):one(wire_fields(v),2) for v in msg.get(9,[])}
        result.update(server_id_matches=True,full_cid_match=one(msg,1)==packet.full_cid,
            short_cid_match=one(msg,5)==packet.short_cid,content_match=one(msg,8)==packet.text,
            sender_match=one(msg,7)==packet.sender_id,client_id_match=ext.get(b's:client_message_id')==packet.client_id,
            text_type_match=one(msg,6)==1000)
        break
    return result
