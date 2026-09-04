// ComfyUI-VRAM-DeepClean —— 透传槽位动态显隐
// 后端固定声明 8 路「任意数据/透传数据」(*),此脚本只做视觉层的显隐:
//   - 默认只显示第 1 路(空闲)
//   - 每接入一路,自动亮出下一个空闲槽位(视觉上"连一个长一个")
//   - 拔掉中间某一路:已连接的槽位永远可见,不隐藏
// JS 不加载/不生效时:全部 8 路直接显示,功能完全不受影响(优雅降级)。
import { app } from "../../scripts/app.js";

const MAX = 8;

app.registerExtension({
  name: "VRAMDeepClean.DynamicSlots",
  async beforeRegisterNodeDef(nodeType, nodeData, appIns) {
    if (nodeType.comfyClass !== "VRAMDeepRelease" && nodeData?.name !== "VRAMDeepRelease") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
      const node = this;

      node.vdcUpdate = function () {
        try {
          // 找到已连接的最大槽位号
          let maxUsed = 0;
          for (const inp of node.inputs || []) {
            const m = /^任意数据(\d+)$/.exec(inp.name || "");
            if (m && inp.link != null) maxUsed = Math.max(maxUsed, parseInt(m[1], 10));
          }
          // 显示 1..(maxUsed+1),其余隐藏;已连接的永不隐藏
          for (let i = 1; i <= MAX; i++) {
            const show = i <= maxUsed + 1;
            const inp = (node.inputs || []).find((x) => x.name === "任意数据" + i);
            const out = (node.outputs || []).find((x) => x.name === "透传数据" + i);
            if (inp) inp.hidden = !show && inp.link == null;
            if (out) out.hidden = !show;
          }
          if (typeof node.setSize === "function" && node.computeSize) {
            node.setSize(node.computeSize());
          }
        } catch (e) { /* 静默:显隐失败不影响功能 */ }
      };

      const origConn = node.onConnectionsChange;
      node.onConnectionsChange = function () {
        const r2 = origConn ? origConn.apply(node, arguments) : undefined;
        node.vdcUpdate();
        return r2;
      };

      const origCfg = node.onConfigure;
      node.onConfigure = function () {
        const r3 = origCfg ? origCfg.apply(node, arguments) : undefined;
        setTimeout(() => node.vdcUpdate && node.vdcUpdate(), 0);
        return r3;
      };

      setTimeout(() => node.vdcUpdate && node.vdcUpdate(), 0);
      return r;
    };
  },
});
