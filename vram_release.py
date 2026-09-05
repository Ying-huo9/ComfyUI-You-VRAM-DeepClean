# -*- coding: utf-8 -*-
"""显存彻底释放 (VRAM Deep Clean) —— 独立通用节点。

三层释放:
  ① ComfyUI 注册模型: unload_all_models() + soft_empty_cache()
     (等同 ComfyUI-Manager「释放模型和节点缓存」按钮 / Memory_Cleanup 节点的全部能力)
  ② 进程内 GGUF 深度扫描: gc 找出内存里所有 llama_cpp.Llama 实例
     —— 不论被哪个插件持有(自建 / llama-TE / llama-cpp_vllm / 未来任何插件),
     逐个 close() 并把持有它的字典/列表槽位断开引用
     (这是 ComfyUI-Manager 够不着的部分,也是本节点存在的意义)
  ③ 终态清扫: gc.collect() + torch.cuda.empty_cache()

节点带 OUTPUT_NODE 标记,但有输入守卫:
  - 串在链路中间或挂在工作流最后(输入有连线)→ 正常执行,清显存;
  - 空挂在工作流里(一根输入线都没接)→ run() 检测到后自动跳过,
    不会抢跑卸载其它节点正在使用的模型(这是老版本崩溃的根源)。
带 8 路「任意数据」透传口(*) —— 任意类型、任意数量,1:1 原样透传,
从上一环拉线进来即插入执行位置,兼作执行顺序控制器
(思路同 Control Order & Free Memory;前端 JS 自动隐藏未用槽位,
连一个长一个,JS 不生效时最多显示 8 路,功能不受影响)。

零第三方依赖(torch/llama_cpp 均按需导入,缺失时对应层自动跳过)。
"""

import gc


# ---------------------------------------------------------------------------
# HTTP 接口(供前端「立即释放」按钮直接调用,不走运行队列)
# ---------------------------------------------------------------------------

async def _http_release_endpoint(request):
    """POST /vramdeepclean/release  —— 三层释放并返回 JSON 报告。"""
    from aiohttp import web
    deep = True
    try:
        data = await request.json()
        deep = bool(data.get("deep", True)) if isinstance(data, dict) else True
    except Exception:
        pass
    report, freed = do_release(deep)
    print("[VRAM深度释放][按钮] " + report)
    return web.json_response({"ok": True, "report": report, "freed": freed})


def _register_route():
    try:
        from server import PromptServer
        if PromptServer.instance is None:
            return
        PromptServer.instance.routes.post("/vramdeepclean/release")(_http_release_endpoint)
        print("[VRAM深度释放] 已注册 /vramdeepclean/release 接口(节点按钮可用)")
    except Exception as e:
        print("[VRAM深度释放] HTTP 接口注册失败(按钮将不可用): %s" % e)


_register_route()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _vram_snapshot():
    """返回 {free, total} 字节;torch/CUDA 不可用时返回 None。"""
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return {"free": free, "total": total}
    except Exception:
        pass
    return None


def _fmt_gb(n):
    return "%.2fGB" % (n / 1024 ** 3)


# ---------------------------------------------------------------------------
# 三层释放
# ---------------------------------------------------------------------------

def _release_comfyui_models():
    """① ComfyUI 注册模型(Checkpoint/VAE/CLIP/LoRA/GGUF-Unet 等官方体系内的一切)。"""
    msgs = []
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        msgs.append("ComfyUI 注册模型已全部卸载")
    except Exception as e:
        msgs.append("ComfyUI 注册模型卸载跳过(%s)" % e.__class__.__name__)
    return msgs


def _release_llama_instances():
    """② gc 深度扫描: 释放进程内所有 llama_cpp.Llama(任何插件的进程内 GGUF)。

    返回 [(模型路径, 断开的引用数), ...];llama_cpp 未安装时返回 []。
    """
    released = []
    try:
        import llama_cpp
    except ImportError:
        return released

    targets = [o for o in gc.get_objects() if isinstance(o, llama_cpp.Llama)]
    for obj in targets:
        name = getattr(obj, "model_path", None) or "?"
        # close() 让 llama.cpp 立即归还自己的显存分配(不经过 torch 分配器)
        try:
            if hasattr(obj, "close"):
                obj.close()
        except Exception:
            pass
        # 断开所有字典/列表槽位对它的强引用(插件缓存 dict、模块全局、__dict__ 等),
        # 让下一轮 gc.collect() 真正回收对象
        cleared = 0
        try:
            for ref in gc.get_referrers(obj):
                if isinstance(ref, dict):
                    for k, v in list(ref.items()):
                        if v is obj:
                            ref[k] = None
                            cleared += 1
                elif isinstance(ref, list):
                    for i, v in enumerate(ref):
                        if v is obj:
                            ref[i] = None
                            cleared += 1
        except Exception:
            pass
        released.append((name, cleared))
    return released


def _final_sweep():
    """③ 终态清扫。"""
    gc.collect()
    try:
        import comfy.model_management as mm
        mm.soft_empty_cache()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 统一释放入口(节点 run 与 HTTP 按钮接口共用)
# ---------------------------------------------------------------------------

def do_release(deep=True):
    """执行三层释放,返回 (报告文本, 释放字节数)。"""
    before = _vram_snapshot()
    msgs = []

    # ① ComfyUI 注册模型
    msgs += _release_comfyui_models()

    # ② 进程内 GGUF 深度扫描
    if deep:
        rel = _release_llama_instances()
        if rel:
            for name, n in rel:
                short = str(name).replace("\\", "/").rsplit("/", 1)[-1]
                msgs.append("GGUF 已释放: %s (断开引用 %d 处)" % (short, n))
        else:
            msgs.append("深度扫描: 未发现进程内 GGUF 模型")

    # ③ 终态清扫
    _final_sweep()

    # 报告
    after = _vram_snapshot()
    freed = 0
    if before and after:
        freed = max(after["free"] - before["free"], 0)
        msgs.append("显存空闲 %s → %s (本次释放 %s)"
                    % (_fmt_gb(before["free"]),
                       _fmt_gb(after["free"]),
                       _fmt_gb(freed)))
    return " | ".join(msgs), freed


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------

_MAX_SLOTS = 8  # 透传槽位数(后端固定声明,前端 JS 自动隐藏未用的)


class VRAMDeepRelease:
    """显存彻底释放 —— Manager 能放的它都放,Manager 不能放的(进程内 GGUF)它也放。"""

    @classmethod
    def INPUT_TYPES(cls):
        optional = {"任意数据%d" % i: ("*", {}) for i in range(1, _MAX_SLOTS + 1)}
        return {
            "required": {
                "深度扫描": ("BOOLEAN", {"default": True,
                    "tooltip": "开启: gc 扫描并释放进程内所有 llama.cpp GGUF 模型"
                               "(自建加载器/llama-TE/任何插件加载的)。"
                               "关闭: 只清理 ComfyUI 注册模型(等同 Manager 按钮)。"}),
            },
            "optional": optional,
        }

    RETURN_TYPES = ("*",) * _MAX_SLOTS
    RETURN_NAMES = tuple("透传数据%d" % i for i in range(1, _MAX_SLOTS + 1))
    FUNCTION = "run"
    # OUTPUT_NODE + 输入守卫: 输入有连线才会真正释放(串中间/挂最后都行);
    # 空挂不连线则 run() 跳过 —— 兼得"接到最后"与"不抢跑"。
    OUTPUT_NODE = True
    CATEGORY = "VRAM清理"

    def run(self, 深度扫描=True, **kwargs):
        # 输入守卫: 8 路透传全部未接 → 视为空挂,跳过释放(防抢跑崩溃)
        if all(kwargs.get("任意数据%d" % i) is None for i in range(1, _MAX_SLOTS + 1)):
            print("[VRAM深度释放] 未接任何输入,跳过执行(空挂保护);"
                  "手动清理请点节点上的「立即释放显存」按钮")
            return tuple(None for _ in range(_MAX_SLOTS))
        report, _freed = do_release(bool(深度扫描))
        print("[VRAM深度释放] " + report)
        # 透传数据 1:1 原样送出(未接的槽位输出 None)
        return tuple(kwargs.get("任意数据%d" % i) for i in range(1, _MAX_SLOTS + 1))


NODE_CLASS_MAPPINGS = {"VRAMDeepRelease": VRAMDeepRelease}
NODE_DISPLAY_NAME_MAPPINGS = {"VRAMDeepRelease": "显存彻底释放 (VRAM Deep Clean)"}
