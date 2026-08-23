import { useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import GuideViewer from '../components/GuideViewer'
import { USER_GUIDE } from '../platformDocs'

export default function GuidePage() {
  const { slug } = useParams()
  const navigate = useNavigate()
  const selectChapter = useCallback((nextSlug, options = {}) => {
    navigate(`/guide/${nextSlug}`, { replace: Boolean(options.replace) })
  }, [navigate])

  return (
    <GuideViewer
      documents={USER_GUIDE}
      audience="user"
      activeSlug={slug || USER_GUIDE[0]?.slug}
      onSelect={selectChapter}
    />
  )
}
