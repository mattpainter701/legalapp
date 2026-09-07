import { useEffect, useState } from 'react'
import { addUserAlias, deleteUserAlias, getUserAliases } from '../../api'

export default function UserAliases({ user }) {
  const [aliases, setAliases] = useState([])
  const [address, setAddress] = useState('')
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(null)
  const load = () => getUserAliases(user.id).then(setAliases).catch(() => setError('Unable to load aliases'))
  useEffect(() => { if (open) load() }, [open, user.id])
  const add = async (event) => {
    event.preventDefault()
    if (!address.trim()) return
    try { await addUserAlias(user.id, address.trim()); setAddress(''); setError(null); load() }
    catch (e) { setError(e?.response?.data?.detail || 'Unable to add alias') }
  }
  return <div className="mt-2">
    <button type="button" onClick={() => setOpen((v) => !v)} className="text-xs text-brand-muted hover:text-brand-ink">{open ? 'Hide aliases' : 'Manage aliases'}</button>
    {open && <div className="mt-2 space-y-2">
      {aliases.map((alias) => <div key={alias.id} className="flex items-center gap-2 text-xs"><span>{alias.address}</span><span className={alias.is_verified ? 'text-green-700' : 'text-amber-700'}>{alias.is_verified ? 'Verified' : 'Pending verification'}</span><button type="button" onClick={() => deleteUserAlias(user.id, alias.id).then(load)} className="text-brand-rose">Remove</button></div>)}
      <form onSubmit={add} className="flex gap-2"><input aria-label={`Alias address for ${user.email}`} type="email" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="send-as@firm.com" className="px-2 py-1 border border-brand-line rounded text-xs" /><button type="submit" className="px-2 py-1 bg-brand-ink text-white rounded text-xs">Add</button></form>
      {error && <p className="text-xs text-brand-rose">{error}</p>}
    </div>}
  </div>
}
