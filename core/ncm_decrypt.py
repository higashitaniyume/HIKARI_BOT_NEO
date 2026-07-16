"""
NCM (NetEase Cloud Music) 文件解密工具 - Python 实现 (修正版)

基于对解密页面 decrypt.js 源码的逆向分析与修复，实现了完整的 NCM 解密流程。

主要修正:
1. 修复了 Key Box 生成算法: 原始 JS 代码在 PRGA 映射过程中并不改变 (Swap) S 盒，
   而是以只读形式进行 S 盒의 二次查表映射。原 Python 实现错误地复用了标准的 RC4 PRGA (包含了交换和状态改变)。
2. 修复了元数据解密算法: NCM 容器内的元数据块在经过 XOR 0x63 异或并截取前 22 字节后，
   实际上是以 Base64 格式编码的 ASCII 文本。解密前必须进行 Base64 解码，本版本对其进行了正确解码。
3. 增加了自动的专辑封面 (Cover Art) 提取以及对 Mutagen 库 (若已安装) 的歌曲元数据与封面图写入支持。
"""

import struct
import json
import base64
import binascii
import os
import logging
from pathlib import Path

logger = logging.getLogger("ncm_decrypt")

# ===================== 常量定义 =====================

# NCM 文件魔数 (8 字节)
NCM_MAGIC = b"CTENFDAM"

# 解密核心密钥 (AES-128-ECB, 16字节)
CORE_KEY = binascii.a2b_hex("687a4852416d736f356b496e62617857")

# 解密元数据密钥 (AES-128-ECB, 16字节)
META_KEY = binascii.a2b_hex("2331346C6A6B5F215C5D2630553C2728")

# ===================== 辅助函数 =====================

def _pkcs7_unpad(data: bytes) -> bytes:
    """去除 PKCS7 填充"""
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        return data
    if all(b == pad_len for b in data[-pad_len:]):
        return data[:-pad_len]
    return data

def _create_key_box(key_data: bytes) -> bytes:
    """
    根据密钥数据生成 Key Box (RC4-like 256字节密钥盒)
    对应 JS 源码: t.prototype._getKeyBox
    """
    # KSA - Key Scheduling Algorithm (打乱 S 盒)
    S = list(range(256))
    key_len = len(key_data)
    j = 0
    for i in range(256):
        j = (j + S[i] + key_data[i % key_len]) & 0xFF
        S[i], S[j] = S[j], S[i]

    # PRGA - Pseudo-Random Generation Algorithm (JS 映射等效，不改变 S 盒内容)
    # JS: return r.map(function(e, t, r) { t = t + 1 & 255; var i = r[t], n = r[t + i & 255]; return r[i + n & 255] })
    result = bytearray(256)
    for idx in range(256):
        t = (idx + 1) & 0xFF
        i = S[t]
        n = S[(t + i) & 0xFF]
        result[idx] = S[(i + n) & 0xFF]

    return bytes(result)

def _guess_format(audio_data: bytes) -> str:
    """根据音频文件头部特征猜测格式"""
    if audio_data[:3] == b'\xff\xfb' or audio_data[:2] == b'\xff\xf3' or audio_data[:2] == b'\xff\xf2':
        return "mp3"
    elif audio_data[:4] == b'fLaC':
        return "flac"
    elif audio_data[:4] == b'OggS':
        return "ogg"
    elif audio_data[:4] == b'ftyp' or audio_data[4:8] == b'ftyp':
        return "m4a"
    elif audio_data[:4] == b'RIFF':
        return "wav"
    else:
        return "mp3"

def _write_metadata(file_path: str, meta_info: dict, cover_data: bytes = None):
    """
    使用 mutagen 库向生成的音频文件中写入元数据和专辑封面
    """
    try:
        import mutagen
    except ImportError:
        logger.warning("[提示] 未检测到 mutagen 库，无法向生成文件中写入专辑封面与元数据标签。")
        logger.warning("[提示] 如需自动打标签，请运行: pip install mutagen")
        return

    ext = Path(file_path).suffix.lower()
    if ext == ".mp3":
        try:
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, APIC, TPE1, TIT2, TALB
            audio = MP3(file_path, ID3=ID3)
            try:
                audio.add_tags()
            except Exception:
                pass
            
            # 写入标题、歌手、专辑
            if 'musicName' in meta_info:
                audio.tags.add(TIT2(encoding=3, text=meta_info['musicName']))
            if 'artist' in meta_info:
                artists = [a[0] for a in meta_info['artist'] if isinstance(a, (list, tuple))] if isinstance(meta_info['artist'], list) else []
                if not artists and isinstance(meta_info['artist'], list):
                    artists = [str(a) for a in meta_info['artist']]
                audio.tags.add(TPE1(encoding=3, text=" / ".join(artists)))
            if 'album' in meta_info:
                audio.tags.add(TALB(encoding=3, text=meta_info['album']))
            
            # 写入封面
            if cover_data:
                audio.tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # Front cover
                    desc='Cover',
                    data=cover_data
                ))
            audio.save()
            logger.info("  -> 成功写入 MP3 元数据和封面。")
        except Exception as e:
            logger.error(f"  -> 写入 MP3 元数据失败: {e}")

    elif ext == ".flac":
        try:
            from mutagen.flac import FLAC, Picture
            audio = FLAC(file_path)
            
            if 'musicName' in meta_info:
                audio['title'] = meta_info['musicName']
            if 'artist' in meta_info:
                artists = [a[0] for a in meta_info['artist'] if isinstance(a, (list, tuple))] if isinstance(meta_info['artist'], list) else []
                if not artists and isinstance(meta_info['artist'], list):
                    artists = [str(a) for a in meta_info['artist']]
                audio['artist'] = artists
            if 'album' in meta_info:
                audio['album'] = meta_info['album']
                
            if cover_data:
                picture = Picture()
                picture.data = cover_data
                picture.type = 3
                picture.mime = 'image/jpeg'
                picture.desc = 'Cover'
                audio.clear_pictures()
                audio.add_picture(picture)
                
            audio.save()
            logger.info("  -> 成功写入 FLAC 元数据和封面。")
        except Exception as e:
            logger.error(f"  -> 写入 FLAC 元数据失败: {e}")

# ===================== 核心解密 =====================

def decrypt_ncm(data: bytes, filename: str = "") -> dict:
    """
    解密完整的 NCM 文件的二进制数据
    """
    try:
        from Crypto.Cipher import AES
    except ImportError:
        raise ImportError(
            "解密依赖 PyCryptodome 库，请先安装: pip install pycryptodome"
        )

    view = memoryview(data)
    offset = 0

    # 1. 验证魔数
    magic = bytes(view[offset:offset + 8])
    if magic != NCM_MAGIC:
        raise ValueError(f"无效的 NCM 文件: 魔数不匹配 (got {magic!r}, expected {NCM_MAGIC!r})")
    offset += 10  # 跳过 8字节魔数 + 2字节保留区

    # 2. 获取并解密 AES 核心密钥
    key_length = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    # XOR 0x64
    encrypted_key = bytes(b ^ 0x64 for b in view[offset:offset + key_length])
    offset += key_length

    # AES-128-ECB 解密
    cipher_key = AES.new(CORE_KEY, AES.MODE_ECB)
    decrypted_key = cipher_key.decrypt(encrypted_key)
    decrypted_key = _pkcs7_unpad(decrypted_key)
    
    # 截取前 17 字节后的内容作为真正的 Key Box 密钥数据
    key_data = decrypted_key[17:]

    # 3. 解析并解密歌曲元数据 (Metadata)
    meta_length = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    meta_info = {}
    if meta_length > 0:
        # XOR 0x63
        encrypted_meta_raw = bytes(b ^ 0x63 for b in view[offset:offset + meta_length])
        offset += meta_length

        # 截取 22 字节之后的部分并进行 Base64 解码
        try:
            meta_base64 = encrypted_meta_raw[22:]
            encrypted_meta = base64.b64decode(meta_base64)

            # AES-128-ECB 解密元数据
            cipher_meta = AES.new(META_KEY, AES.MODE_ECB)
            decrypted_meta = cipher_meta.decrypt(encrypted_meta)
            decrypted_meta = _pkcs7_unpad(decrypted_meta)

            meta_str = decrypted_meta.decode('utf-8', errors='replace')
            # 通用处理协议前缀: `music:{...}`, `dj:{...}` 等格式
            # 找到第一个冒号，取其后的内容作为 JSON
            colon_idx = meta_str.find(':')
            if colon_idx != -1:
                prefix = meta_str[:colon_idx]
                meta_str = meta_str[colon_idx + 1:]
                meta_info = json.loads(meta_str)
                # 如果是电台歌曲 (dj:)，解密结果包含 mainMusic 子结构
                if prefix == 'dj' and 'mainMusic' in meta_info:
                    meta_info = meta_info['mainMusic']
            else:
                meta_info = json.loads(meta_str)
        except Exception as e:
            logger.warning(f"解析元数据失败: {e}，将使用文件名作为歌曲标题。")

    # 4. 创建 Key Box (S 盒二次映射流)
    key_box = _create_key_box(key_data)

    # 5. 提取专辑封面图数据 (Cover Art)
    # NCM 格式容器结构 (元数据之后):
    #   [4 bytes CRC32] [1 byte padding] [4 bytes image_length] [4 bytes image_length(重复)] [image_data]
    # 即: image_length 在 offset+5，image_data 实际从 offset+13 开始
    image_length = struct.unpack_from("<I", data, offset + 5)[0]
    
    cover_data = None
    if image_length > 0:
        cover_data = bytes(view[offset + 13 : offset + 13 + image_length])
    
    # 更新偏移以跳过封面区域并对齐音频数据区
    # 总计跳过: 13 (CRC+padding+两次length字段) + image_length 字节
    offset += image_length + 13

    # 6. 解密音频数据
    audio_encrypted = bytes(view[offset:])
    audio_data = bytearray(len(audio_encrypted))
    
    # 逐字节与 key_box 循环 XOR
    for i in range(len(audio_encrypted)):
        audio_data[i] = audio_encrypted[i] ^ key_box[i & 0xFF]
        
    audio_data = bytes(audio_data)

    # 7. 获取音频格式
    audio_format = meta_info.get("format", "")
    if not audio_format:
        audio_format = _guess_format(audio_data)

    return {
        "title": meta_info.get("musicName", "") or Path(filename).stem,
        "artist": "; ".join([a[0] for a in meta_info.get("artist", []) if isinstance(a, (list, tuple))]) or "",
        "album": meta_info.get("album", ""),
        "format": audio_format,
        "audio_data": audio_data,
        "cover_data": cover_data,
        "meta_info": meta_info
    }

# ===================== 应用层 API =====================

def decrypt_file(input_path: str, output_dir: str = None, rename_by_meta: bool = False) -> str:
    """
    解密单个 NCM 文件
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"未找到输入文件: {input_path}")

    if not output_dir:
        output_dir = input_path.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    with open(input_path, "rb") as f:
        ncm_data = f.read()

    # 解密
    result = decrypt_ncm(ncm_data, filename=input_path.name)

    # 生成输出文件名
    if rename_by_meta and (result["title"] or result["artist"]):
        artist_str = f" - {result['artist']}" if result["artist"] else ""
        out_name = f"{result['title']}{artist_str}.{result['format']}"
    else:
        out_name = f"{input_path.stem}.{result['format']}"

    out_path = output_dir / out_name

    with open(out_path, "wb") as f:
        f.write(result["audio_data"])

    # 写入元数据标签
    _write_metadata(str(out_path), result["meta_info"], result["cover_data"])

    return str(out_path)

def batch_decrypt(input_dir: str, output_dir: str = None, recursive: bool = False, rename_by_meta: bool = False):
    """
    批量解密指定目录下的所有 NCM 文件
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise NotADirectoryError(f"无效的输入目录: {input_dir}")

    pattern = "**/*.ncm" if recursive else "*.ncm"
    files = list(input_dir.glob(pattern))

    if not files:
        print(f"在 {input_dir} 中未找到任何 .ncm 文件。")
        return

    print(f"找到 {len(files)} 个待解密的 .ncm 文件，开始解密...")
    success_count = 0
    
    for idx, file_path in enumerate(files, 1):
        try:
            print(f"[{idx}/{len(files)}] 正在解密: {file_path.name}")
            out_file = decrypt_file(file_path, output_dir, rename_by_meta)
            print(f"  ✓ 已保存: {Path(out_file).name}")
            success_count += 1
        except Exception as e:
            print(f"  ✗ 解密失败 {file_path.name}: {e}")

    print(f"\n批量解密完成: {success_count} 成功, {len(files) - success_count} 失败")

# ===================== CLI 入口 =====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="网易云音乐 NCM 格式解密工具 (修正版)")
    parser.add_argument("input", help="输入的 NCM 文件路径或包含 NCM 文件的文件夹路径")
    parser.add_argument("-o", "--output", help="输出文件夹，如果不指定则默认在原文件同级目录下生成")
    parser.add_argument("-r", "--recursive", action="store_true", help="如果输入是文件夹，是否递归子目录进行批量解密")
    parser.add_argument("-n", "--rename", action="store_true", help="使用歌曲元数据 (歌名 - 歌手) 重命名输出文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细的日志调试信息")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    path = Path(args.input)
    if not path.exists():
        print(f"错误: 输入路径不存在 - {args.input}")
        exit(1)

    try:
        if path.is_file():
            print(f"正在处理单个文件: {path.name}")
            out = decrypt_file(path, args.output, args.rename)
            print(f"解密成功！输出文件: {out}")
        elif path.is_dir():
            batch_decrypt(path, args.output, args.recursive, args.rename)
    except Exception as e:
        print(f"\n[错误] 执行过程中发生异常: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        exit(1)
