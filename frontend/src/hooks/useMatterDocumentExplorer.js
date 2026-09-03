import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createMatterDocumentFolder,
  deleteMatterDocumentFolder,
  getDocumentTags,
  getMatterDocumentFolders,
  getMatterDocuments,
  moveMatterDocuments,
  updateMatterDocumentFolder,
} from '../api'

export const ROOT_FOLDER = 'root'
export const ALL_DOCUMENTS = 'all'

/**
 * State for the matter document explorer.
 *
 * The document list is server-filtered: folder scope, search, tag filter and
 * sort all round-trip so a matter with thousands of files never has to ship
 * every row to the browser to render one folder.
 */
export function useMatterDocumentExplorer(matterId) {
  const [folders, setFolders] = useState([])
  const [tags, setTags] = useState([])
  const [documents, setDocuments] = useState([])
  const [rootDocumentCount, setRootDocumentCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [listing, setListing] = useState(false)
  const [error, setError] = useState(null)

  const [folderId, setFolderId] = useState(ALL_DOCUMENTS)
  const [includeSubfolders, setIncludeSubfolders] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedTagIds, setSelectedTagIds] = useState([])
  const [sort, setSort] = useState('created_at')
  const [order, setOrder] = useState('desc')

  // Only the newest list response may write to state; a slow earlier request
  // must not overwrite the folder the user has since clicked into.
  const requestSeq = useRef(0)

  const listParams = useMemo(() => {
    const params = { sort, order }
    if (folderId !== ALL_DOCUMENTS) {
      params.folder_id = folderId
      if (folderId !== ROOT_FOLDER && includeSubfolders) params.include_subfolders = true
    }
    if (search.trim()) params.q = search.trim()
    if (selectedTagIds.length) params.tag_ids = selectedTagIds
    return params
  }, [folderId, includeSubfolders, search, selectedTagIds, sort, order])

  const refreshFolders = useCallback(async () => {
    if (!matterId) return
    const data = await getMatterDocumentFolders(matterId)
    setFolders(data.items || [])
    setRootDocumentCount(data.root_document_count || 0)
  }, [matterId])

  const refreshTags = useCallback(async () => {
    const data = await getDocumentTags()
    setTags(data.items || [])
  }, [])

  const refreshDocuments = useCallback(async () => {
    if (!matterId) return
    const seq = ++requestSeq.current
    setListing(true)
    try {
      const data = await getMatterDocuments(matterId, listParams)
      if (seq === requestSeq.current) setDocuments(data.items || [])
    } finally {
      if (seq === requestSeq.current) setListing(false)
    }
  }, [matterId, listParams])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      getMatterDocumentFolders(matterId),
      getDocumentTags().catch(() => ({ items: [] })),
    ])
      .then(([folderData, tagData]) => {
        if (cancelled) return
        setFolders(folderData.items || [])
        setRootDocumentCount(folderData.root_document_count || 0)
        setTags(tagData.items || [])
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load folders.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [matterId])

  useEffect(() => {
    refreshDocuments().catch(() => setError('Failed to load documents.'))
  }, [refreshDocuments])

  const foldersByParent = useMemo(() => {
    const grouped = new Map()
    for (const folder of folders) {
      const key = folder.parent_id || ROOT_FOLDER
      if (!grouped.has(key)) grouped.set(key, [])
      grouped.get(key).push(folder)
    }
    for (const siblings of grouped.values()) {
      siblings.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
    }
    return grouped
  }, [folders])

  const currentFolder = useMemo(
    () => folders.find((f) => f.id === folderId) || null,
    [folders, folderId],
  )

  const breadcrumb = useMemo(() => {
    if (!currentFolder) return []
    const byId = new Map(folders.map((f) => [f.id, f]))
    const trail = []
    let node = currentFolder
    while (node) {
      trail.unshift(node)
      node = node.parent_id ? byId.get(node.parent_id) : null
    }
    return trail
  }, [currentFolder, folders])

  const createFolder = useCallback(
    async (name, parentId = null) => {
      const folder = await createMatterDocumentFolder(matterId, {
        name,
        parent_id: parentId,
      })
      await refreshFolders()
      return folder
    },
    [matterId, refreshFolders],
  )

  const renameFolder = useCallback(
    async (id, name) => {
      const folder = await updateMatterDocumentFolder(matterId, id, { name })
      await refreshFolders()
      return folder
    },
    [matterId, refreshFolders],
  )

  const reparentFolder = useCallback(
    async (id, parentId) => {
      const folder = await updateMatterDocumentFolder(matterId, id, {
        parent_id: parentId,
      })
      await refreshFolders()
      return folder
    },
    [matterId, refreshFolders],
  )

  const removeFolder = useCallback(
    async (id, { moveDocumentsToParent = false } = {}) => {
      const result = await deleteMatterDocumentFolder(matterId, id, {
        moveDocumentsToParent,
      })
      // The open folder just disappeared; fall back to the whole matter rather
      // than leaving the list scoped to something that no longer exists.
      setFolderId((current) => (current === id ? ALL_DOCUMENTS : current))
      await refreshFolders()
      await refreshDocuments()
      return result
    },
    [matterId, refreshFolders, refreshDocuments],
  )

  const fileDocuments = useCallback(
    async (documentIds, targetFolderId) => {
      const result = await moveMatterDocuments(
        matterId,
        documentIds,
        targetFolderId === ROOT_FOLDER ? null : targetFolderId,
      )
      await refreshFolders()
      await refreshDocuments()
      return result
    },
    [matterId, refreshFolders, refreshDocuments],
  )

  const toggleTagFilter = useCallback((tagId) => {
    setSelectedTagIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    )
  }, [])

  return {
    // data
    folders,
    foldersByParent,
    tags,
    documents,
    rootDocumentCount,
    currentFolder,
    breadcrumb,
    // status
    loading,
    listing,
    error,
    setError,
    // filters
    folderId,
    setFolderId,
    includeSubfolders,
    setIncludeSubfolders,
    search,
    setSearch,
    selectedTagIds,
    setSelectedTagIds,
    toggleTagFilter,
    sort,
    setSort,
    order,
    setOrder,
    // actions
    createFolder,
    renameFolder,
    reparentFolder,
    removeFolder,
    fileDocuments,
    refreshFolders,
    refreshTags,
    refreshDocuments,
    setDocuments,
  }
}

export default useMatterDocumentExplorer
