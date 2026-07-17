# Git 协作与推送规范手册

> 本手册旨在统一团队（Android, 后端, RAG）的 Git 协作流程，确保代码历史整洁、可追溯，并减少合并冲突。

---

## 一、 分支管理模型：GitHub Flow

团队采用 **GitHub Flow** 模型：
- **`main` 分支**：主分支，始终保持可运行、可交付的状态。
- **`develop` 分支**：开发主分支，所有功能分支汇聚于此。
- **功能分支**：从 `develop` 检出，完成后合并回 `develop`。

---

## 二、 分支命名规范

**格式**：`类型/姓名/功能描述`

### 1. 常用类型 (Type)
- `feat`: 新功能 (feature)
- `fix`: 修补 bug
- `docs`: 文档变更 (documentation)
- `style`: 代码格式调整（不影响逻辑）
- `refactor`: 重构（既不是新增功能也不是修补 bug）
- `perf`: 性能优化
- `test`: 增加测试
- `chore`: 构建过程或辅助工具的变动

### 2. 示例
- `feat/zhangsan/add-image-search`
- `docs/lisi/update-rag-api`
- `fix/wangwu/fix-upload-timeout`

---

## 三、 Commit Message 规范

采用 **Conventional Commits** 风格，使用 **中文** 编写。

**格式**：`<类型>: <描述>`

### 1. 示例
- `feat: 接入 CLIP 模型实现图像特征提取`
- `docs: 更新 RAG 模块对接接口确认单`
- `fix: 修复 Android 端大图上传内存溢出的问题`
- `refactor: 重构后端编排层的异常处理逻辑`

---

## 四、 开发与推送流程

### 1. 开始开发
```powershell
# 切换到 develop 分支并拉取最新代码
git checkout develop
git pull origin develop

# 创建并切换到个人功能分支
git checkout -b feat/yourname/your-feature
```

### 2. 提交代码
```powershell
# 建议小步快跑，频繁 commit
git add .
git commit -m "feat: 具体的改动描述"
```

### 3. 同步远程
```powershell
# 在推送前，建议先拉取 develop 并在本地合并，解决冲突
git fetch origin
git merge origin/develop

# 推送到远程仓库
git push -u origin feat/yourname/your-feature
```

---

## 五、 合并规范 (PR/MR)

1. **发起 Pull Request (PR)**：
   - 将 `个人分支` 合并到 `develop` 分支。
   - 标题简述功能，描述中 @ 相关队友进行 Review。

2. **合并策略：Squash Merge (压缩合并)**：
   - **操作**：在 GitHub 点击合并按钮时，选择 **"Squash and merge"**。
   - **目的**：将功能分支上杂乱的多次提交压缩为一个整洁的提交记录合并到主干，保持 `develop/main` 历史线性且清晰。

---

## 六、 禁忌事项

1. **禁止直接推送 `main` 或 `develop` 分支**。
2. **禁止提交二进制大文件**（如 50MB 以上的模型文件），请使用 Git LFS 或外部存储。
3. **禁止在未解决冲突的情况下强推 (`--force`)**。
4. **禁止提交敏感信息**（如 API Key, 数据库密码），请务必放入 `.env` 并确保 `.env` 在 `.gitignore` 中。
