import NextAuth from 'next-auth'
import CredentialsProvider from 'next-auth/providers/credentials'

const handler = NextAuth({
  providers: [
    CredentialsProvider({
      name: 'Credentials',
      credentials: {
        email:    { label: 'Email',    type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        if (
          credentials?.email    === 'nike@southbay.com.ar' &&
          credentials?.password === 'Nike2026!'
        ) {
          return {
            id:    '1',
            email: 'nike@southbay.com.ar',
            name:  'Nike Argentina',
            image: null,
          }
        }
        return null
      },
    }),
  ],
  pages: {
    signIn:  '/login',
    error:   '/login',
  },
  session: { strategy: 'jwt', maxAge: 8 * 60 * 60 },
  secret: process.env.NEXTAUTH_SECRET ?? 'nike-dashboard-secret-2026',
  callbacks: {
    async jwt({ token, user }) {
      if (user) token.user = user
      return token
    },
    async session({ session, token }) {
      session.user = token.user as typeof session.user
      return session
    },
  },
})

export { handler as GET, handler as POST }
